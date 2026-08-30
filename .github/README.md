# GitHub Configuration

This directory contains VS Code agent customization files for the NextWave project.

## Structure

- **copilot-instructions.md** - Main workspace-level instructions for the Copilot agent. Includes project context, working style, domain knowledge, and communication guidelines.
- **AGENTS.md** - Custom agent definitions (if any)
- **CLAUDE.md** - Claude-specific configuration
- **agents/** - Directory for custom agent definitions
- **instructions/** - Directory for file-specific instructions
- **hooks/** - Directory for lifecycle hooks

## Configuration Files

### copilot-instructions.md
The primary configuration file that Copilot loads for this workspace. Contains:
- Project context and goals
- Working style and communication preferences
- Domain knowledge (payment orchestration, conversion rates, etc.)
- Decision heuristics and anti-patterns
- Expected team workflow

This file is automatically loaded by GitHub Copilot when working in this VS Code workspace.

## Adding More Customizations

To add custom agents, file-specific instructions, or hooks:

1. **Custom Agents**: Create `.agent.md` files in the `agents/` directory
2. **File Instructions**: Create `*.instructions.md` files in the `instructions/` directory
3. **Lifecycle Hooks**: Create `.json` hook files in the `hooks/` directory

Refer to the [Agent Customization Skill](https://code.visualstudio.com/docs/copilot/custom-instructions) for detailed guidance.
