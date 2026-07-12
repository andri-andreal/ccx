use std::path::{Path, PathBuf};

pub fn ccx_home_from(get: impl Fn(&str) -> Option<String>) -> PathBuf {
    if let Some(h) = get("CCX_HOME").filter(|s| !s.is_empty()) {
        return PathBuf::from(h);
    }
    if let Some(x) = get("XDG_CONFIG_HOME").filter(|s| !s.is_empty()) {
        return PathBuf::from(x).join("ccx");
    }
    let home = get("HOME").unwrap_or_default();
    PathBuf::from(home).join(".config").join("ccx")
}

pub fn ccx_home() -> PathBuf {
    ccx_home_from(|k| std::env::var(k).ok())
}

pub fn profiles_dir(home: &Path) -> PathBuf {
    home.join("profiles")
}
pub fn profile_dir(home: &Path, name: &str) -> PathBuf {
    home.join("profiles").join(name)
}
pub fn profile_env_path(home: &Path, name: &str) -> PathBuf {
    profile_dir(home, name).join("profile.env")
}
pub fn providers_dir(home: &Path) -> PathBuf {
    home.join("providers")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn resolver<'a>(pairs: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<String> + 'a {
        move |k: &str| {
            pairs
                .iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| v.to_string())
        }
    }

    #[test]
    fn ccx_home_prefers_ccx_home() {
        let get = resolver(&[
            ("CCX_HOME", "/tmp/x"),
            ("XDG_CONFIG_HOME", "/home/u/.config"),
            ("HOME", "/home/u"),
        ]);
        assert_eq!(ccx_home_from(get), PathBuf::from("/tmp/x"));
    }

    #[test]
    fn ccx_home_falls_back_to_xdg() {
        let get = resolver(&[("XDG_CONFIG_HOME", "/home/u/.config"), ("HOME", "/home/u")]);
        assert_eq!(ccx_home_from(get), PathBuf::from("/home/u/.config/ccx"));
    }

    #[test]
    fn ccx_home_falls_back_to_home_dotconfig() {
        let get = resolver(&[("HOME", "/home/u")]);
        assert_eq!(ccx_home_from(get), PathBuf::from("/home/u/.config/ccx"));
    }

    #[test]
    fn path_helpers_compose() {
        let h = PathBuf::from("/c");
        assert_eq!(
            profile_env_path(&h, "mm"),
            PathBuf::from("/c/profiles/mm/profile.env")
        );
        assert_eq!(providers_dir(&h), PathBuf::from("/c/providers"));
    }
}
