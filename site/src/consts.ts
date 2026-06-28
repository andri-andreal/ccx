export const SITE_TITLE = 'ccx — Claude Code profile/provider switcher';
export const SITE_DESCRIPTION =
  'Launch Claude Code under named profiles. Switch providers and models ' +
  '(Anthropic, MiniMax, GLM, DeepSeek, Kimi) via env vars — no patching. ' +
  'OpenAI-compatible backends (OpenAI, OpenRouter, Ollama, vLLM, LM Studio) ' +
  'via a managed local router. CLI + desktop GUI.';

export const REPO_URL = 'https://github.com/andri-andreal/claude-code-profile-switcher';

// kind: 'anthropic' = direct /v1/messages; 'router' = OpenAI-compatible via the built-in ccx-router.
export const PROVIDERS = [
  { id: 'claude', name: 'Anthropic', note: 'opusplan; your normal login', kind: 'anthropic' },
  { id: 'minimax', name: 'MiniMax', note: 'MiniMax-M3 / M2', kind: 'anthropic' },
  { id: 'glm', name: 'GLM (Z.ai)', note: 'Anthropic-compatible', kind: 'anthropic' },
  { id: 'deepseek', name: 'DeepSeek', note: 'Anthropic-compatible', kind: 'anthropic' },
  { id: 'kimi', name: 'Kimi (Moonshot)', note: 'Anthropic-compatible', kind: 'anthropic' },
  { id: 'custom', name: 'Custom', note: 'any Anthropic-compatible endpoint', kind: 'anthropic' },
  { id: 'openai', name: 'OpenAI', note: 'via ccx-router', kind: 'router' },
  { id: 'openrouter', name: 'OpenRouter', note: 'via ccx-router', kind: 'router' },
  { id: 'ollama', name: 'Ollama', note: 'local models via ccx-router', kind: 'router' },
  { id: 'vllm', name: 'vLLM', note: 'local models via ccx-router', kind: 'router' },
  { id: 'lmstudio', name: 'LM Studio', note: 'local models via ccx-router', kind: 'router' },
  { id: 'custom-oai', name: 'Custom (OpenAI)', note: 'any OpenAI-compatible endpoint', kind: 'router' },
];

export const FEATURES = [
  {
    title: 'Named profiles',
    body: 'One command per provider/model combo — `ccx glm`, `ccx claude`, `ccx m3`.',
  },
  {
    title: 'Isolated credentials',
    body: 'Third-party profiles each get their own CLAUDE_CONFIG_DIR; logins and history never mix.',
  },
  {
    title: 'No patching',
    body: 'Claude Code runs unmodified. Everything is driven by environment variables.',
  },
  {
    title: 'Pick the model at creation',
    body: '`ccx new` prompts for the model (or pass --model), filling every slot cleanly.',
  },
  {
    title: 'Editable provider defaults',
    body: 'Endpoints and model IDs live in simple template files you can tweak.',
  },
  {
    title: 'CLI + desktop GUI',
    body: 'Manage profiles from the terminal or a Tauri desktop app — fully interchangeable.',
  },
];

export const DOCS_NAV = [
  { href: '/docs/installation/', label: 'Installation' },
  { href: '/docs/usage/', label: 'Usage' },
  { href: '/docs/providers/', label: 'Providers' },
  { href: '/docs/desktop-gui/', label: 'Desktop GUI' },
  { href: '/docs/roadmap/', label: 'Roadmap' },
];
