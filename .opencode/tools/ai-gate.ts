import { tool } from "@opencode-ai/plugin"

export default tool({
  description: `Run prove-it-ai-gate acceptance checks on the current session.

Self-gate the agent's work before claiming completion. Checks transcript
truncation, workspace hygiene, evidence folder, confidence claims, and more.

Returns ACCEPT, ACCEPT_WITH_CONDITIONS, REJECT, or BLOCKED.
If REJECT or BLOCKED, fix the issues and call this tool again.`,

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
      .describe("Path to session transcript JSONL")
      .default("./transcript.jsonl"),
  },

  async execute(args, context) {
    const cmd = [
      "python", "-m", "prove_it_ai_gate.cli", "accept",
      "--repo", context.worktree,
      "--evidence", args.evidence,
      "--transcript", args.transcript,
      "--task-type", args.taskType,
    ]

    try {
      const proc = Bun.spawnSync(cmd, {
        cwd: context.worktree,
        stdout: "pipe",
        stderr: "pipe",
      })

      const stdout = proc.stdout?.toString() ?? ""
      const stderr = proc.stderr?.toString() ?? ""

      if (proc.exitCode !== 0 && proc.exitCode !== 1 && proc.exitCode !== 2 && proc.exitCode !== 3) {
        return `ai-gate failed (exit ${proc.exitCode}): ${stderr || stdout || "unknown error"}`
      }

      return stdout || stderr || "ai-gate completed with no output"
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      if (msg.includes("ENOENT") || msg.includes("not found")) {
        return "prove-it-ai-gate is not installed. Run: pip install prove-it-ai-gate"
      }
      return `ai-gate error: ${msg}`
    }
  },
})
