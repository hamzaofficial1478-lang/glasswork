"""
service.py — the always-on crew. Run under a process manager (pm2/systemd) on the
VPS so it survives reboots. These are the agents that must react in real time and
can't wait for the daily cron:

  - Reception  (replies to enquiries within a minute — this wins jobs)
  - Watcher    (uptime / error monitoring)
  - Responder  (outreach replies)
  - Security   (screens inbound as it arrives)

In production these graduate onto the Claude Agent SDK so each runs its own loop,
loads the MCP servers, and streams events. Sketch of one loop:

    from claude_agent_sdk import Agent          # pip install claude-agent-sdk
    reception = Agent(system="agents/reception.md", mcp_servers=[...])
    async for event in reception.run_forever():
        handle(event)

Start simple: run this file with a plain polling loop and graduate to the SDK.
"""
if __name__ == "__main__":
    print("Run the always-on agents here (Reception, Watcher, Responder, Security).")
