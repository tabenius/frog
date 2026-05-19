from __future__ import annotations


TOP_LEVEL = (
    "db new agent box doctor board tui whereis setup provider gh import export hook "
    "agent-instructions completion ps snapshot status log config mcp repo unit task lock file sync"
)


def completion_script(
    shell: str,
    *,
    repo_actions: set[str],
    repo_names: str,
    workspace_names: list[str],
    registered_files: str,
) -> str:
    repo_subs = "list register discover sync info task key keys dep affected " + " ".join(sorted(repo_actions))
    workspace_words = " ".join(workspace_names)
    if shell == "bash":
        return f"""_frog_complete() {{
  local cur
  _init_completion || return
  local top="{TOP_LEVEL}"
  local repo_subs="{repo_subs}"
  local repo_names="{repo_names}"
  local registered_files="{registered_files}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "$top" -- "$cur") )
    return
  fi
  case "${{COMP_WORDS[1]}}" in
    completion) COMPREPLY=( $(compgen -W "bash fish" -- "$cur") ) ;;
    ps|snapshot|status) COMPREPLY=() ;;
    doctor) COMPREPLY=( $(compgen -W "--no-fix" -- "$cur") ) ;;
    db) COMPREPLY=( $(compgen -W "migrate schema gc" -- "$cur") ) ;;
    new|agent-instructions) COMPREPLY=( $(compgen -d -- "$cur") ) ;;
    config)
      if [[ $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "info host workspace coordinator path" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 && "${{COMP_WORDS[2]}}" == "host" ]]; then
        COMPREPLY=( $(compgen -W "add list" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 && "${{COMP_WORDS[2]}}" == "workspace" ]]; then
        COMPREPLY=( $(compgen -W "add list use" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 && "${{COMP_WORDS[2]}}" == "coordinator" ]]; then
        COMPREPLY=( $(compgen -W "show set" -- "$cur") )
      elif [[ $COMP_CWORD -eq 4 && "${{COMP_WORDS[2]}}" == "coordinator" && "${{COMP_WORDS[3]}}" == "set" ]]; then
        COMPREPLY=( $(compgen -W "{workspace_words}" -- "$cur") )
      elif [[ $COMP_CWORD -eq 4 && "${{COMP_WORDS[2]}}" == "workspace" && "${{COMP_WORDS[3]}}" == "use" ]]; then
        COMPREPLY=( $(compgen -W "{workspace_words}" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 && "${{COMP_WORDS[2]}}" == "path" ]]; then
        COMPREPLY=( $(compgen -W "bash fish" -- "$cur") )
      fi
      ;;
    mcp)
      [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "serve tools" -- "$cur") ) ;;
    repo)
      if [[ $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "$repo_subs" -- "$cur") )
      elif [[ $COMP_CWORD -eq 3 ]]; then
        COMPREPLY=( $(compgen -W "$repo_names" -- "$cur") )
      fi
      ;;
    unit) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "discover list" -- "$cur") ) ;;
    task) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "create list next claim finish info status dependency conflict tag assign" -- "$cur") ) ;;
    lock) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "check acquire renew release list info" -- "$cur") ) ;;
    file)
      if [[ $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "upsert list info" -- "$cur") )
      elif [[ "${{COMP_WORDS[2]}}" == "info" ]]; then
        COMPREPLY=( $(compgen -W "$registered_files" -- "$cur") )
        [[ ${{#COMPREPLY[@]}} -eq 0 ]] && COMPREPLY=( $(compgen -f -- "$cur") )
      elif [[ "${{COMP_WORDS[2]}}" == "upsert" ]]; then
        COMPREPLY=( $(compgen -f -- "$cur") )
      fi
      ;;
    provider) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "pull outbox sync" -- "$cur") ) ;;
    hook) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "add list remove dispatch digest" -- "$cur") ) ;;
    box) [[ $COMP_CWORD -eq 2 ]] && COMPREPLY=( $(compgen -W "whoami peers join" -- "$cur") ) ;;
    log) COMPREPLY=( $(compgen -W "why blame --follow --limit --repo --all -f" -- "$cur") ) ;;
  esac
}}
complete -F _frog_complete frog
"""
    if shell == "fish":
        return f"""function __frog_complete_registered_files
    command frog --no-color --no-pager --json file list 2>/dev/null | python3 -c 'import json,sys; data=json.load(sys.stdin); [print(item.get("file_path","")) for item in data.get("files",[]) if item.get("file_path")]' 2>/dev/null
end

complete -c frog -f
complete -c frog -n '__fish_use_subcommand' -a '{TOP_LEVEL}'
complete -c frog -n '__fish_seen_subcommand_from completion' -a 'bash fish'
complete -c frog -n '__fish_seen_subcommand_from doctor' -a '--no-fix'
complete -c frog -n '__fish_seen_subcommand_from db' -a 'migrate schema gc'
complete -c frog -n '__fish_seen_subcommand_from new agent-instructions' -a '(__fish_complete_directories)'
complete -c frog -n '__fish_seen_subcommand_from config' -a 'info host workspace coordinator path'
complete -c frog -n '__fish_seen_subcommand_from path' -a 'bash fish'
complete -c frog -n '__fish_seen_subcommand_from mcp' -a 'serve tools'
complete -c frog -n '__fish_seen_subcommand_from host' -a 'add list'
complete -c frog -n '__fish_seen_subcommand_from workspace' -a 'add list use'
complete -c frog -n '__fish_seen_subcommand_from coordinator' -a 'show set'
complete -c frog -n '__fish_seen_subcommand_from repo' -a '{repo_subs}'
complete -c frog -n '__fish_seen_subcommand_from {" ".join(sorted(repo_actions))} info' -a '{repo_names}'
complete -c frog -n '__fish_seen_subcommand_from unit' -a 'discover list'
complete -c frog -n '__fish_seen_subcommand_from task' -a 'create list next claim finish info status dependency conflict tag assign'
complete -c frog -n '__fish_seen_subcommand_from lock' -a 'check acquire renew release list info'
complete -c frog -n '__fish_seen_subcommand_from file; and not __fish_seen_subcommand_from upsert list info' -a 'upsert list info'
complete -c frog -n '__fish_seen_subcommand_from file; and __fish_seen_subcommand_from info' -a '(__frog_complete_registered_files)'
complete -c frog -n '__fish_seen_subcommand_from file; and __fish_seen_subcommand_from info' -F
complete -c frog -n '__fish_seen_subcommand_from file; and __fish_seen_subcommand_from upsert' -F
complete -c frog -n '__fish_seen_subcommand_from provider' -a 'pull outbox sync'
complete -c frog -n '__fish_seen_subcommand_from hook' -a 'add list remove dispatch digest'
complete -c frog -n '__fish_seen_subcommand_from box' -a 'whoami peers join'
complete -c frog -n '__fish_seen_subcommand_from log' -a 'why blame --follow --limit --repo --all -f'
"""
    raise ValueError(f"unsupported shell: {shell}")
