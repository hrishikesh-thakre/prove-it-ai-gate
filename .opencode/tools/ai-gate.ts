import { tool } from "@opencode-ai/plugin"

export default tool({
  description: `Run prove-it-ai-gate acceptance checks on the current agent session.

This tool validates whether the agent's work has enough evidence to be accepted.
It checks transcript truncation, workspace hygiene, evidence completeness,
confidence claims, scope completeness, and heuristic extraction.

Returns ACCEPT, ACCEPT_WITH_CONDITIONS, REJECT, or BLOCKED.`,

  args: {
    taskType: tool.schema
      .string()
      .describe("Task type: audit, code_change, corpus_audit")
      .default("audit"),
    evidence: tool.schema
      .string()
      .describe("Path to evidence folder")
      .default("./evidence"),
    transcript: tool.schema
      .string()
      .describe("Path to the session transcript JSONL file")
      .default("./transcript.jsonl"),
  },

  async execute(args, context) {
    const proc = Bun.spawnSync(
      [
        "python", "-m", "prove_it_ai_gate.cli", "accept",
        "--repo", context.worktree,
        "--evidence", args.evidence,
        "--transcript", args.transcript,
        "--task-type", args.taskType,
      ],
      { cwd: context.worktree }
    )

    return proc.stdout.toString() + proc.stderr.toString()
  },
})
