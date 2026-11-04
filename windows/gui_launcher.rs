//! Native Windows launcher used only by windows/build_gui.py.
use crate::profile::Profile;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone, PartialEq)]
pub struct TerminalSpec {
    pub bin: String,
    pub exec_args: Vec<String>,
}

pub fn find_executable(bin: &str) -> Option<PathBuf> {
    let path = Path::new(bin);
    if path.is_absolute() && path.is_file() {
        return Some(path.to_path_buf());
    }
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths).find_map(|dir| {
            ["", ".exe", ".cmd", ".bat"]
                .iter()
                .map(|ext| dir.join(format!("{bin}{ext}")))
                .find(|p| p.is_file())
        })
    })
}

pub fn is_in_path(bin: &str) -> bool {
    find_executable(bin).is_some()
}

pub fn detect_terminal(
    term_env: Option<&str>,
    available: impl Fn(&str) -> bool,
) -> Option<TerminalSpec> {
    for bin in term_env
        .filter(|v| !v.is_empty())
        .into_iter()
        .chain(["wt.exe", "cmd.exe"])
    {
        if available(bin) {
            let args = if Path::new(bin)
                .file_stem()
                .is_some_and(|s| s.eq_ignore_ascii_case("wt"))
            {
                vec!["new-tab".to_string(), "--".to_string()]
            } else {
                vec!["/d".to_string(), "/k".to_string()]
            };
            return Some(TerminalSpec {
                bin: bin.to_string(),
                exec_args: args,
            });
        }
    }
    None
}

pub fn copy_command(p: &Profile) -> String {
    format!("ccx {}", p.name)
}

pub fn ccx_command(bin: &str) -> Result<Command, String> {
    let executable = find_executable(bin).ok_or("ccx not found; run install.ps1 -AddToPath")?;
    if executable
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("cmd"))
    {
        let script = executable.with_extension("py");
        if !script.is_file() {
            return Err("CCX_BIN must point to ccx.cmd, ccx.py or an executable".into());
        }
        let mut command = if is_in_path("py.exe") {
            let mut c = Command::new("py.exe");
            c.arg("-3");
            c
        } else {
            Command::new("python.exe")
        };
        command.args(["-X", "utf8"]).arg(script);
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW for diagnostics
        Ok(command)
    } else if executable
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("py"))
    {
        let mut command = Command::new("python.exe");
        command
            .args(["-X", "utf8"])
            .arg(executable)
            .creation_flags(0x08000000);
        Ok(command)
    } else {
        Ok(Command::new(executable))
    }
}

pub fn launch(p: &Profile, _home: &Path, term_override: Option<Vec<String>>) -> Result<(), String> {
    if p.name.is_empty()
        || !p
            .name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err("Invalid profile name".into());
    }
    let spec = match term_override {
        Some(args) if !args.is_empty() => TerminalSpec {
            bin: args[0].clone(),
            exec_args: args[1..].to_vec(),
        },
        _ => detect_terminal(std::env::var("TERMINAL").ok().as_deref(), is_in_path)
            .ok_or("No Windows terminal found")?,
    };
    let mut cmd = Command::new(&spec.bin);
    cmd.args(&spec.exec_args);
    if Path::new(&spec.bin)
        .file_stem()
        .is_some_and(|s| s.eq_ignore_ascii_case("wt"))
    {
        cmd.args(["cmd.exe", "/d", "/k"]);
    }
    cmd.args(["ccx", &p.name])
        .creation_flags(0x00000010)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn detects_windows_terminal_and_cmd_fallback() {
        assert_eq!(
            detect_terminal(None, |b| b == "wt.exe").unwrap().exec_args,
            vec!["new-tab", "--"]
        );
        assert_eq!(
            detect_terminal(None, |b| b == "cmd.exe").unwrap().exec_args,
            vec!["/d", "/k"]
        );
        assert!(detect_terminal(None, |_| false).is_none());
    }
    #[test]
    fn validates_name_before_opening_terminal() {
        let p = Profile {
            name: "x & echo bad".into(),
            ..Default::default()
        };
        assert!(launch(&p, Path::new("."), None).is_err());
    }
    #[test]
    fn diagnostics_use_python_instead_of_batch_shell() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("ccx.cmd");
        std::fs::write(&path, "@echo off").unwrap();
        std::fs::write(path.with_extension("py"), "print('fixture')").unwrap();
        let cmd = ccx_command(path.to_str().unwrap()).unwrap();
        assert!(cmd.get_args().any(|a| a == "utf8"));
        assert!(cmd
            .get_args()
            .any(|a| a.to_string_lossy().ends_with("ccx.py")));
    }
}
