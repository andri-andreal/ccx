use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GuiSettings {
    pub terminal_override: Option<Vec<String>>,
}

fn settings_path(home: &Path) -> PathBuf {
    home.join("gui.json")
}

pub fn load(home: &Path) -> GuiSettings {
    fs::read_to_string(settings_path(home))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save(home: &Path, s: &GuiSettings) -> Result<(), String> {
    fs::create_dir_all(home).map_err(|e| e.to_string())?;
    let json = serde_json::to_string_pretty(s).map_err(|e| e.to_string())?;
    fs::write(settings_path(home), json).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_returns_default_when_missing() {
        let tmp = tempfile::tempdir().unwrap();
        assert_eq!(load(tmp.path()), GuiSettings::default());
    }

    #[test]
    fn save_then_load_round_trips() {
        let tmp = tempfile::tempdir().unwrap();
        let s = GuiSettings {
            terminal_override: Some(vec!["alacritty".into(), "-e".into()]),
        };
        save(tmp.path(), &s).unwrap();
        assert_eq!(load(tmp.path()), s);
    }
}
