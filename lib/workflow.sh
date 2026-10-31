# Sourced by bin/ccx. Workflow presets contain profile references, never secrets.
choose_workflow_profile() {
  local target_role="$1" title="$2" preferred="${3:-}"
  [ -t 0 ] || die "choose $target_role with --$target_role when not running in a terminal"
  local -a choices=() descriptions=()
  local directory profile file provider model
  # Put the planner first for the builder menu, preserving its default.
  if [ -n "$preferred" ] && [ -f "$(profile_env "$preferred")" ]; then
    choices+=("$preferred")
  fi
  for directory in "$(profiles_dir)"/*/; do
    [ -d "$directory" ] || continue
    profile="$(basename "$directory")"
    valid_profile_name "$profile" || continue
    [ -f "$directory/profile.env" ] || continue
    [ "$profile" != "$preferred" ] || continue
    choices+=("$profile")
  done
  [ "${#choices[@]}" -gt 0 ] || die "no eligible $target_role profiles; create model profiles with ccx new first"
  for profile in "${choices[@]}"; do
    file="$(profile_env "$profile")"
    provider="$(kv_get "$file" CCX_PROVIDER)"
    case "$provider" in
      codex) model="$(kv_get "$file" CODEX_MODEL)" ;;
      kimi-cli|mcode) model="$(kv_get "$file" CCX_NATIVE_MODEL)" ;;
      *) model="$(kv_get "$file" ANTHROPIC_MODEL)" ;;
    esac
    descriptions+=("${provider:-custom} · ${model:-CLI default}")
  done
  tui_subtitles=("${descriptions[@]}")
  tui_select "$title" "${choices[@]}" || die 'profile creation cancelled'
  printf -v "$target_role" '%s' "${choices[$tui_selected]}"
}

choose_workflow_model() {
  local role="$1" profile="$2" variable="${1}_model" provider file saved candidate
  file="$(profile_env "$profile")"
  provider="$(kv_get "$file" CCX_PROVIDER)"
  saved="$(kv_get "$file" ANTHROPIC_MODEL)"
  [ "$provider" != codex ] || saved="$(kv_get "$file" CODEX_MODEL)"
  case "$provider" in kimi-cli|mcode) saved="$(kv_get "$file" CCX_NATIVE_MODEL)" ;; esac
  local -a choices=(default) descriptions=("${saved:-CLI default}")
  case "$provider" in
    claude) choices+=(sonnet opus haiku); descriptions+=('Claude Sonnet' 'Claude Opus' 'Claude Haiku') ;;
    codex)
      while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        choices+=("$candidate"); descriptions+=('Codex local catalog')
      done < <(python3 "$SCRIPT_DIR/../lib/model_catalog.py" models "$file") ;;
  esac
  choices+=('Custom model ID'); descriptions+=('Enter an exact model ID')
  tui_subtitles=("${descriptions[@]}")
  tui_select "Choose $role model ($profile):" "${choices[@]}" || die 'profile creation cancelled'
  candidate="${choices[$tui_selected]}"
  if [ "$candidate" = 'Custom model ID' ]; then
    printf 'Model ID: '; read -r candidate
    [ -n "$candidate" ] && [ "$candidate" != default ] || die 'model ID must not be empty or default'
  fi
  [ "$candidate" != default ] || candidate=''
  printf -v "$variable" '%s' "$candidate"
}

choose_workflow_effort() {
  local role="$1" profile="$2" variable="${1}_effort" provider
  provider="$(kv_get "$(profile_env "$profile")" CCX_PROVIDER)"
  local -a levels=(default low medium high xhigh)
  if [ "$provider" = kimi-cli ]; then
    printf -v "$variable" %s default; return
  fi
  if [ "$provider" = codex ]; then
    local model_key="${role}_model" selected_model="" level
    selected_model="${!model_key}"
    [ -n "$selected_model" ] || selected_model="$(kv_get "$(profile_env "$profile")" CODEX_MODEL)"
    local -a supported=()
    while IFS= read -r level; do [ -z "$level" ] || supported+=("$level"); done < <(
      python3 "$SCRIPT_DIR/../lib/model_catalog.py" efforts "$(profile_env "$profile")" "$selected_model")
    if [ "${#supported[@]}" -gt 0 ]; then levels=(default "${supported[@]}")
    else levels=(default minimal low medium high xhigh); fi
  else levels+=(max); fi
  tui_subtitles=()
  tui_select "Choose $role effort ($profile):" "${levels[@]}" || die 'profile creation cancelled'
  printf -v "$variable" '%s' "${levels[$tui_selected]}"
}

create_workflow() {
  local role value
  local -a roles=(planner builder reviewer)
  if [ -n "$orchestrator" ] || [ -z "$yes" ]; then roles=(orchestrator planner builder reviewer); fi
  for role in "${roles[@]}"; do
    if [ -z "$yes" ] && [ -z "${!role}" ]; then
      case "$role" in
        orchestrator) choose_workflow_profile orchestrator 'Choose orchestrator:' ;;
        planner) choose_workflow_profile planner 'Choose planner:' ;;
        builder) choose_workflow_profile builder 'Choose builder:' "$planner" ;;
        reviewer) choose_workflow_profile reviewer 'Choose checker (reviewer):' ;;
      esac
    fi
    [ "$role" != builder ] || builder="${builder:-$planner}"
    value="${!role}"
    valid_profile_name "$value" || die "invalid $role profile: $value"
    [ -f "$(profile_env "$value")" ] || die "unknown $role model profile: $value (create it with ccx new first)"
    local effort_key="${role}_effort" model_key="${role}_model" effort provider
    if [ -z "$yes" ] && [ -z "${!model_key}" ]; then
      choose_workflow_model "$role" "$value"
    fi
    [[ "${!model_key}" != *$'\n'* && "${!model_key}" != *$'\r'* ]] || die 'model ID must be a single line'
    if [ -z "$yes" ] && [ -z "${!effort_key}" ]; then
      choose_workflow_effort "$role" "${!role}"
    fi
    effort="${!effort_key}"
    provider="$(kv_get "$(profile_env "${!role}")" CCX_PROVIDER)"
    [ "$provider" != kimi-cli ] || [ -z "$effort" ] || [ "$effort" = default ] || die "Kimi CLI supports only default effort"
    case "$effort" in
      ''|default|low|medium|high|xhigh) ;;
      minimal) [ "$provider" = codex ] || die 'minimal effort requires Codex' ;;
      max) ;;
      ultra) [ "$provider" = codex ] || die 'ultra effort requires Codex' ;;
      *) die "invalid $role effort: $effort" ;;
    esac
  done
  for value in "$rounds" "$fix_rounds" "$timeout"; do
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || die 'rounds, fix-rounds and timeout must be positive integers'
  done
  [[ "$proof" != *$'\n'* && "$proof" != *$'\r'* ]] || die 'verification command must be a single line'
  local pdir; pdir="$(profile_dir "$name")"
  mkdir -p "$pdir"; chmod 700 "$pdir"
  {
    printf 'CCX_TYPE=loop\nCCX_PLANNER=%s\nCCX_BUILDER=%s\nCCX_REVIEWER=%s\n' "$planner" "$builder" "$reviewer"
    if [ -n "$orchestrator" ]; then
      printf 'CCX_ORCHESTRATOR=%s\nCCX_ORCHESTRATOR_MODEL=%s\nCCX_ORCHESTRATOR_EFFORT=%s\n' "$orchestrator" "$orchestrator_model" "${orchestrator_effort:-default}"
    fi
    printf 'CCX_PROOF=%s\nCCX_ROUNDS=%s\nCCX_FIX_ROUNDS=%s\nCCX_TIMEOUT=%s\n' "$proof" "$rounds" "$fix_rounds" "$timeout"
    printf 'CCX_PLANNER_EFFORT=%s\nCCX_BUILDER_EFFORT=%s\nCCX_REVIEWER_EFFORT=%s\n' "${planner_effort:-default}" "${builder_effort:-default}" "${reviewer_effort:-default}"
    printf 'CCX_PLANNER_MODEL=%s\nCCX_BUILDER_MODEL=%s\nCCX_REVIEWER_MODEL=%s\n' "$planner_model" "$builder_model" "$reviewer_model"
  } > "$pdir/workflow.env"
  chmod 600 "$pdir/workflow.env"
  printf 'Created workflow "%s". Run it: ccx %s "your task"\n' "$name" "$name"
  [ -n "$proof" ] || printf 'Verification: automatically detected from the target project when run.\n'
}
