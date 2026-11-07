use std::path::PathBuf;

fn run() -> Result<(), String> {
    let mut args = std::env::args_os().skip(1);
    let command = args
        .next()
        .ok_or("expected inventory, dry-run, simulate, project, stage or resume subcommand")?;
    let checkpoint_command = command == "stage" || command == "resume";
    if command != "inventory"
        && command != "dry-run"
        && command != "simulate"
        && command != "project"
        && !checkpoint_command
    {
        return Err("usage: frog-model-migrate {inventory|dry-run|simulate|project|stage|resume} --source PATH; project/stage/resume require --workspace ID; stage/resume require --checkpoint PATH".into());
    }
    let (mut source, mut snapshot) = (None, None);
    let mut workspace = None;
    let mut checkpoint = None;
    while let Some(flag) = args.next() {
        if flag == "--workspace" {
            if (command != "project" && !checkpoint_command) || workspace.is_some() {
                return Err(
                    "--workspace is only accepted once for project, stage or resume".into(),
                );
            }
            workspace = Some(
                args.next()
                    .ok_or("missing workspace value")?
                    .into_string()
                    .map_err(|_| "workspace must be UTF-8")?,
            );
            continue;
        }
        let target = match flag.to_str() {
            Some("--source") if source.is_none() => &mut source,
            Some("--snapshot") if !checkpoint_command && snapshot.is_none() => &mut snapshot,
            Some("--checkpoint") if checkpoint_command && checkpoint.is_none() => &mut checkpoint,
            _ => return Err("unknown or duplicate migration option".into()),
        };
        *target = Some(PathBuf::from(args.next().ok_or("missing option value")?));
    }
    let source = source.ok_or("--source is required; no implicit database selection")?;
    let result = if checkpoint_command {
        let workspace = workspace
            .as_deref()
            .ok_or("stage/resume require explicit --workspace ID")?;
        let checkpoint = checkpoint
            .as_deref()
            .ok_or("stage/resume require explicit --checkpoint PATH")?;
        let report = if command == "stage" {
            frog_model::migration_checkpoint::stage(&source, workspace, checkpoint)?
        } else {
            frog_model::migration_checkpoint::resume(&source, workspace, checkpoint)?
        };
        serde_json::to_string_pretty(&report)
    } else if command == "inventory" {
        serde_json::to_string_pretty(&frog_model::migration::inventory(
            &source,
            snapshot.as_deref(),
        )?)
    } else if command == "dry-run" {
        serde_json::to_string_pretty(&frog_model::migration_dry_run::dry_run(
            &source,
            snapshot.as_deref(),
        )?)
    } else if command == "simulate" {
        serde_json::to_string_pretty(&frog_model::migration_simulation::simulate(
            &source,
            snapshot.as_deref(),
        )?)
    } else {
        serde_json::to_string_pretty(&frog_model::migration_projection::project(
            &source,
            workspace
                .as_deref()
                .ok_or("project requires explicit --workspace ID")?,
            snapshot.as_deref(),
        )?)
    };
    println!("{}", result.map_err(|e| e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(2);
    }
}
