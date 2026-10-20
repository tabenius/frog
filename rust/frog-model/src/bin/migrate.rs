use std::path::PathBuf;

fn run() -> Result<(), String> {
    let mut args = std::env::args_os().skip(1);
    if args.next().as_deref() != Some(std::ffi::OsStr::new("inventory")) {
        return Err("usage: frog-model-migrate inventory --source PATH [--snapshot NEW_PATH]".into());
    }
    let (mut source, mut snapshot) = (None, None);
    while let Some(flag) = args.next() {
        let target = match flag.to_str() {
            Some("--source") if source.is_none() => &mut source,
            Some("--snapshot") if snapshot.is_none() => &mut snapshot,
            _ => return Err("unknown or duplicate inventory option".into()),
        };
        *target = Some(PathBuf::from(args.next().ok_or("missing option value")?));
    }
    let source = source.ok_or("--source is required; no implicit database selection")?;
    let result = frog_model::migration::inventory(&source, snapshot.as_deref())?;
    println!("{}", serde_json::to_string_pretty(&result).map_err(|e| e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(2);
    }
}
