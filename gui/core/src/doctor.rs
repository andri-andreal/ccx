use std::collections::BTreeMap;
use std::process::Command;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CheckStatus {
    Pass,
    Warn,
    Fail,
    Skip,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DoctorSummary {
    pub passed: u32,
    pub warnings: u32,
    pub failed: u32,
    pub skipped: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DoctorCheck {
    pub id: String,
    pub label: String,
    pub status: CheckStatus,
    pub message: String,
    pub required: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DoctorReport {
    pub schema_version: u32,
    pub command: String,
    pub profile: Option<String>,
    pub status: CheckStatus,
    pub summary: DoctorSummary,
    pub checks: Vec<DoctorCheck>,
    #[serde(default)]
    pub capabilities: BTreeMap<String, CheckStatus>,
}

pub fn parse_report(stdout: &str) -> Result<DoctorReport, String> {
    let report: DoctorReport = serde_json::from_str(stdout)
        .map_err(|_| "ccx returned an invalid doctor report".to_string())?;
    if report.schema_version != 1 {
        return Err(format!(
            "unsupported ccx doctor schema version: {}",
            report.schema_version
        ));
    }
    Ok(report)
}

fn run_command(
    profile: &str,
    command: &str,
    consent: bool,
    no_network: bool,
) -> Result<DoctorReport, String> {
    if profile.is_empty()
        || !profile
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err("invalid profile name".to_string());
    }

    let bin = std::env::var("CCX_BIN").unwrap_or_else(|_| "ccx".to_string());
    let mut process = Command::new(&bin);
    process.args([command, profile, "--json"]);
    if consent {
        process.arg("--yes");
    }
    if no_network {
        process.arg("--no-network");
    }
    let output = process
        .env("NO_COLOR", "1")
        .output()
        .map_err(|_| format!("cannot run {bin}; install ccx or set CCX_BIN"))?;

    // A failed diagnostic intentionally exits non-zero. Parse stdout regardless
    // so the GUI can render the individual failures instead of losing them as
    // an IPC error. Stderr is deliberately not surfaced because diagnostics
    // must never leak a provider response or credential.
    parse_report(&String::from_utf8_lossy(&output.stdout))
}

pub fn run(profile: &str) -> Result<DoctorReport, String> {
    run_command(profile, "doctor", false, false)
}

pub fn run_offline(profile: &str) -> Result<DoctorReport, String> {
    run_command(profile, "doctor", false, true)
}

pub fn certify(profile: &str) -> Result<DoctorReport, String> {
    // The frontend owns the explicit consent dialog. `--yes` only suppresses
    // the CLI's stdin prompt; the probe remains bounded to its minimal checks.
    run_command(profile, "certify", true, false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_versioned_report_and_future_capabilities() {
        let report = parse_report(
            r#"{
                "schema_version": 1,
                "command": "doctor",
                "profile": "local",
                "status": "warn",
                "summary": {"passed": 2, "warnings": 1, "failed": 0, "skipped": 3},
                "checks": [{
                    "id": "profile.model",
                    "label": "Model configured",
                    "status": "pass",
                    "message": "qwen",
                    "required": true
                }],
                "capabilities": {"basic": "unknown", "vision": "skip"}
            }"#,
        )
        .unwrap();

        assert_eq!(report.profile.as_deref(), Some("local"));
        assert_eq!(report.status, CheckStatus::Warn);
        assert_eq!(report.checks[0].status, CheckStatus::Pass);
        assert_eq!(report.capabilities["vision"], CheckStatus::Skip);
    }

    #[test]
    fn rejects_unknown_schema_version() {
        let err = parse_report(
            r#"{"schema_version":2,"command":"doctor","profile":null,"status":"pass","summary":{"passed":0,"warnings":0,"failed":0,"skipped":0},"checks":[],"capabilities":{}}"#,
        )
        .unwrap_err();
        assert!(err.contains("schema version"));
    }

    #[test]
    fn rejects_non_json_output_without_echoing_it() {
        let err = parse_report("secret-token and not json").unwrap_err();
        assert_eq!(err, "ccx returned an invalid doctor report");
        assert!(!err.contains("secret-token"));
    }
}
