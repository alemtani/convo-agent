import { spawn } from "node:child_process"
import path from "node:path"

function run(command, args, extraEnv, cwd) {
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd,
      env: { ...process.env, ...extraEnv },
      stdio: ["ignore", "pipe", "pipe"],
    })
    child.on("close", (code) => resolve(code ?? 1))
    child.on("error", () => resolve(1))
  })
}

async function toast(client, variant, title, message) {
  try {
    await client.tui.showToast({
      title,
      message,
      variant,
      duration: 8000,
    })
  } catch {
    // Headless / no TUI attached.
  }
}

export const SessionGates = async ({ client, directory }) => {
  const seen = new Set()
  const tests = path.join(directory, "scripts/run-tests.sh")
  const tunnel = path.join(directory, "scripts/tunnel.sh")

  async function stopTunnel(sessionID) {
    if (!sessionID) return
    await run(tunnel, ["stop"], { OPENCODE_SESSION_ID: sessionID }, directory)
    seen.delete(sessionID)
  }

  return {
    "shell.env": async (input, output) => {
      if (input.sessionID) {
        seen.add(input.sessionID)
        output.env.OPENCODE_SESSION_ID = input.sessionID
      }
    },
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        const code = await run(tests, [], {}, directory)
        if (code !== 0) {
          await toast(
            client,
            "error",
            "Tests failed",
            "pytest failed. Fix the suite before treating this turn as done.",
          )
        }
      }
      if (event.type === "session.deleted") {
        await stopTunnel(event.properties.info.id)
      }
    },
    dispose: async () => {
      await Promise.all([...seen].map(stopTunnel))
    },
  }
}
