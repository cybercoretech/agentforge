#![allow(missing_docs)]

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn agent_profile_commands_are_deterministic_and_non_mutating() {
    let root = temporary_root();
    let directory = root.join(".forge/agents");
    fs::create_dir_all(&directory).expect("profile directory");
    let executable = PathBuf::from(env!("CARGO_BIN_EXE_forge"));
    fs::write(
        directory.join("local.conf"),
        format!(
            "version=1\nexecutable={}\nargument=--version\nenv.AGENTFORGE_TEST=literal=value\ntimeout_ms=1000\nmax_output_bytes=4096\n",
            executable.display()
        ),
    )
    .expect("profile");

    let listed = forge(&root, &["agent", "list", root.to_str().unwrap()]);
    assert!(listed.status.success(), "{listed:?}");
    assert!(String::from_utf8_lossy(&listed.stdout).contains("id=local"));

    let validated = forge(
        &root,
        &["agent", "validate", root.to_str().unwrap(), "local"],
    );
    assert!(validated.status.success(), "{validated:?}");

    let inspected = forge(
        &root,
        &["agent", "inspect", root.to_str().unwrap(), "local"],
    );
    assert!(inspected.status.success(), "{inspected:?}");
    assert!(String::from_utf8_lossy(&inspected.stdout).contains("arguments=1"));

    fs::write(
        directory.join("broken.conf"),
        format!(
            "version=1\nversion=1\nexecutable={}\n",
            executable.display()
        ),
    )
    .expect("broken profile");
    let rejected = forge(
        &root,
        &["agent", "validate", root.to_str().unwrap(), "broken"],
    );
    assert!(!rejected.status.success(), "{rejected:?}");
    assert!(String::from_utf8_lossy(&rejected.stderr).contains("repeats version"));
    fs::remove_dir_all(root).expect("cleanup");
}

#[test]
fn doctor_agents_reports_profiles_without_running_them() {
    let root = temporary_root();
    let directory = root.join(".forge/agents");
    fs::create_dir_all(&directory).expect("profile directory");
    let forge_binary = PathBuf::from(env!("CARGO_BIN_EXE_forge"));
    let init_target = root.join("init-target");
    fs::create_dir_all(&init_target).expect("init target");
    fs::write(
        directory.join("good.conf"),
        format!(
            "version=1\nexecutable={}\nargument=init\nargument={}\ntimeout_ms=1000\nmax_output_bytes=4096\n",
            forge_binary.display(),
            init_target.display()
        ),
    )
    .expect("good profile");
    fs::write(
        directory.join("quick.conf"),
        format!(
            "version=1\nexecutable={}\nargument=--no-preflight\n",
            forge_binary.display()
        ),
    )
    .expect("quick profile");

    let report = forge(&root, &["doctor", "--agents"]);
    let output = text(&report);
    assert!(report.status.success(), "{output}");
    assert!(output.contains("doctor ok profile good:"), "{output}");
    assert!(
        output.contains("doctor ok profile good executable:"),
        "{output}"
    );
    assert!(
        output.contains("doctor warn profile quick preflight:"),
        "{output}"
    );
    assert!(
        output.contains("agents doctor: 2 profile(s), 0 failing check(s), 1 warning(s)"),
        "{output}"
    );
    assert_eq!(
        fs::read_dir(&init_target).expect("init target").count(),
        0,
        "doctor must not run the profile's executable"
    );

    let plain = forge(&root, &["doctor"]);
    let plain_text = text(&plain);
    assert!(plain.status.success(), "{plain_text}");
    assert!(!plain_text.contains("profile"), "{plain_text}");
    assert!(!plain_text.contains("agents doctor:"), "{plain_text}");

    let elsewhere = temporary_root();
    let from_elsewhere = forge(&elsewhere, &["doctor", root.to_str().unwrap(), "--agents"]);
    let elsewhere_text = text(&from_elsewhere);
    assert!(from_elsewhere.status.success(), "{elsewhere_text}");
    assert!(
        elsewhere_text.contains("doctor ok profile good:"),
        "doctor must observe the given root: {elsewhere_text}"
    );

    fs::remove_dir_all(root).expect("cleanup");
    fs::remove_dir_all(elsewhere).expect("cleanup");
}

#[test]
fn doctor_agents_fails_each_unusable_profile_with_a_fix() {
    let root = temporary_root();
    let directory = root.join(".forge/agents");
    fs::create_dir_all(&directory).expect("profile directory");
    let forge_binary = PathBuf::from(env!("CARGO_BIN_EXE_forge"));
    let absent = root.join("absent-executable");
    fs::write(
        directory.join("missing.conf"),
        format!("version=1\nexecutable={}\n", absent.display()),
    )
    .expect("missing profile");
    fs::write(
        directory.join("paths.conf"),
        format!(
            "version=1\nexecutable={}\nargument={}\n",
            forge_binary.display(),
            absent.display()
        ),
    )
    .expect("argument profile");
    fs::write(
        directory.join("bad name.conf"),
        format!("version=1\nexecutable={}\n", forge_binary.display()),
    )
    .expect("invalid id profile");
    fs::write(
        directory.join("malformed.conf"),
        format!(
            "version=1\nversion=1\nexecutable={}\n",
            forge_binary.display()
        ),
    )
    .expect("malformed profile");

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let script = root.join("not-executable");
        fs::write(&script, "#!/bin/sh\nexit 0\n").expect("script");
        fs::set_permissions(&script, fs::Permissions::from_mode(0o644)).expect("permissions");
        fs::write(
            directory.join("permissions.conf"),
            format!("version=1\nexecutable={}\n", script.display()),
        )
        .expect("permission profile");
    }

    let report = forge(&root, &["doctor", "--agents"]);
    let output = text(&report);
    assert_eq!(report.status.code(), Some(1), "{output}");
    assert!(output.contains("doctor fail profile missing:"), "{output}");
    assert!(
        output.contains("doctor fail profile paths argument:"),
        "{output}"
    );
    assert!(output.contains("bad name"), "{output}");
    assert!(output.contains("repeats version"), "{output}");
    assert!(output.contains("  fix: "), "{output}");
    assert!(!output.contains("panicked"), "{output}");
    #[cfg(unix)]
    assert!(
        output.contains("doctor fail profile permissions executable:")
            && output.contains("is not executable"),
        "{output}"
    );

    fs::remove_dir_all(root).expect("cleanup");
}

#[test]
fn doctor_rejects_invalid_usage() {
    let root = temporary_root();
    let unknown = forge(&root, &["doctor", "--bogus"]);
    assert_eq!(unknown.status.code(), Some(2), "{unknown:?}");
    assert!(
        String::from_utf8_lossy(&unknown.stderr).contains("unknown option --bogus"),
        "{unknown:?}"
    );
    let repeated = forge(&root, &["doctor", "--agents", "--agents"]);
    assert_eq!(repeated.status.code(), Some(2), "{repeated:?}");
    let extra = forge(&root, &["doctor", "one", "two"]);
    assert_eq!(extra.status.code(), Some(2), "{extra:?}");
    fs::remove_dir_all(root).expect("cleanup");
}

fn text(output: &std::process::Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

fn forge(root: &std::path::Path, arguments: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_forge"))
        .args(arguments)
        .current_dir(root)
        .output()
        .expect("forge command")
}

fn temporary_root() -> PathBuf {
    let suffix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "agentforge-cli-agent-profile-{}-{suffix}",
        std::process::id()
    ));
    fs::create_dir(&root).expect("temporary root");
    root
}
