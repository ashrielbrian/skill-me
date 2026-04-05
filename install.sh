#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS=("spring_clean" "rebug" "audit")
DEFAULT_TARGET="$HOME/.claude/skills"

MODE="install"
COPY_MODE=false
FORCE=false
PROJECT_PATH=""

usage() {
    cat <<'EOF'
Usage: ./install.sh [OPTIONS]

Install spring-clean, rebug, and audit skills for Claude Code.

Options:
    --project <path>   Install to <path>/.claude/skills/ instead of ~/.claude/skills/
    --copy             Copy files instead of creating symlinks
    --uninstall        Remove installed skills
    -f, --force        Overwrite existing skills without warning
    -h, --help         Show this help message

Examples:
    ./install.sh                           # Install for all projects (symlink)
    ./install.sh --project ~/my-project    # Install for a specific project
    ./install.sh --copy                    # Copy instead of symlink
    ./install.sh --uninstall               # Remove installed skills
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)
            PROJECT_PATH="$2"
            shift 2
            ;;
        --copy)
            COPY_MODE=true
            shift
            ;;
        --uninstall)
            MODE="uninstall"
            shift
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run ./install.sh --help for usage"
            exit 1
            ;;
    esac
done

if [[ -n "$PROJECT_PATH" ]]; then
    if [[ ! -d "$PROJECT_PATH" ]]; then
        echo "Error: project directory does not exist: $PROJECT_PATH"
        exit 1
    fi
    TARGET_DIR="$PROJECT_PATH/.claude/skills"
else
    TARGET_DIR="$DEFAULT_TARGET"
fi

install_skill() {
    local skill_dir="$1"
    local source="$SCRIPT_DIR/$skill_dir"
    local dest="$TARGET_DIR/$skill_dir"

    if [[ ! -f "$source/SKILL.md" ]]; then
        echo "  Warning: $source/SKILL.md not found, skipping"
        return
    fi

    local skill_name
    skill_name=$(grep '^name:' "$source/SKILL.md" | head -1 | sed 's/name: *//')

    if [[ -L "$dest" ]]; then
        local current_target
        current_target=$(readlink "$dest" 2>/dev/null || true)
        if [[ "$current_target" == "$source" ]]; then
            echo "  /$skill_name — already installed (symlink up to date)"
            return
        fi
        if [[ "$FORCE" != true ]]; then
            echo "  /$skill_name — symlink exists but points to $current_target"
            echo "    Use --force to overwrite"
            return
        fi
        rm "$dest"
    elif [[ -e "$dest" ]]; then
        if [[ "$FORCE" != true ]]; then
            echo "  /$skill_name — already exists at $dest (not a symlink)"
            echo "    Use --force to overwrite"
            return
        fi
        rm -rf "$dest"
    fi

    if [[ "$COPY_MODE" == true ]]; then
        cp -R "$source" "$dest"
        echo "  /$skill_name — copied"
    else
        ln -s "$source" "$dest"
        echo "  /$skill_name — symlinked"
    fi
}

uninstall_skill() {
    local skill_dir="$1"
    local dest="$TARGET_DIR/$skill_dir"

    if [[ ! -e "$dest" && ! -L "$dest" ]]; then
        return
    fi

    local skill_name="$skill_dir"
    if [[ -f "$dest/SKILL.md" ]] || { [[ -L "$dest" ]] && [[ -f "$(readlink "$dest")/SKILL.md" ]]; }; then
        skill_name=$(grep '^name:' "$dest/SKILL.md" 2>/dev/null | head -1 | sed 's/name: *//' || echo "$skill_dir")
    fi

    if [[ -L "$dest" ]]; then
        rm "$dest"
        echo "  /$skill_name — symlink removed"
    elif [[ -d "$dest" ]]; then
        rm -rf "$dest"
        echo "  /$skill_name — directory removed"
    fi
}

if [[ "$MODE" == "install" ]]; then
    mkdir -p "$TARGET_DIR"
    echo "Installing skills to $TARGET_DIR"
    echo ""
    for skill in "${SKILLS[@]}"; do
        install_skill "$skill"
    done
    echo ""
    echo "Done. Available commands in Claude Code:"
    echo "  /spring-clean  — audit a codebase"
    echo "  /rebug         — validate reported issues"
    echo "  /audit         — full pipeline (discover + validate)"
    if [[ "$COPY_MODE" != true ]]; then
        echo ""
        echo "Skills are symlinked — run 'git pull' in this repo to get updates."
    fi
else
    echo "Uninstalling skills from $TARGET_DIR"
    echo ""
    for skill in "${SKILLS[@]}"; do
        uninstall_skill "$skill"
    done
    echo ""
    echo "Done."
fi
