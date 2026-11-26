# Suiii Playground with Claude Code

A local AI playground that runs on your Mac and gets its replies from Claude, through the Claude Code that's already on this Mac and signed in to your own Anthropic account. No API key needed.

## Run it

1. Sign Claude Code in, once. If `claude` works in Terminal, run:

   ```bash
   claude auth login
   ```

   If it doesn't, use the copy inside the Claude desktop app. The server prints this exact command on startup when you're not signed in, and the page shows it under the message box.

2. Start the server (Python 3.9 or newer, no packages to install):

   ```bash
   python3 server.py
   ```

3. Open http://localhost:8787

Stop the server with Ctrl+C. Set `PORT=9000` before the command to use another port. If Claude Code lives somewhere unusual, point the server at it with `CLAUDE_BIN=/path/to/claude`.

The top bar says **Claude Code connected** when it works. It says **Not signed in**, **Claude Code error** or **Server offline** otherwise, and the line under the message box says how to fix it.

## Models and settings

- **Models:** Claude Opus 5.5 (Claude Code's default), Sonnet 5.5, Haiku 5.5 and Fable 5.2. Which ones you can use depends on your plan.
- **Reasoning effort:** Auto (Claude Code's default), Low, Medium, High, XHigh or Max. Claude's thinking shows above each reply when Claude Code shares it.
- **Max output tokens:** up to 128,000. Thinking counts toward it.
- Temperature and Top P aren't offered: Claude Code sets sampling itself.
- Replies use your Claude plan's usage, like Claude Code in Terminal does.

## Deploy to Vercel (not switched yet)

The `api/` folder holds the Vercel functions from the Groq version, and they still call Groq. A hosted site can't use Claude Code, because Claude Code runs on your Mac with your sign-in. Moving the hosted version to Claude would mean calling the Claude API with an API key instead.

## Demo mode

Open `index.html` straight from disk, host it as a static page, or run the server while Claude Code isn't signed in, and the app switches to **demo mode**. Replies are simulated in the browser and labelled as such. Add `?demo=1` to the address to force demo mode.

## The activity panel

The panel above the message box shows what the model is doing, using only real app state. It never shows the model's private reasoning in the panel.

| Mode | Shown when | What you see |
|---|---|---|
| AI core | No tool is switched on, or while the model thinks | The neural core behind a supercar side window, firing with the token stream |
| Web search | Web search is on | A globe with country outlines, the sources it found and which ones were read |
| Code | Code is on (demo mode only for now) | Input → Parse → Execute → Validate → Output, with the model's real code |
| Functions | Functions is on (demo mode only for now) | The core sending a call to the function card and the result coming back |
| Analyze | On the Analyzer page | Score, patterns, risks and fixes from the analysis |
| Templates | The template menu, or inserting a template | The template's name and variables assembling |
| Files | Attaching a file | The file's name, type and size, split into segments |

The **token speed meter** on its right reads 0 to 1,000 tokens per second. While a reply streams it shows a live estimate (about 4 characters per token). After the reply it settles on the average from Claude Code's own token counts and marks the peak.

The pause button stops all animation, and the panel stays still if your system asks for reduced motion. The arrow button minimizes the panel to its status rows.

## How it works

- For each reply, `server.py` runs Claude Code in print mode (`claude -p`) with every built-in tool, MCP server, hook, plugin and slash command turned off, in an empty temporary folder, and without saving the session. Claude Code acts as a plain chat model here: it can't read your files, run commands or use your connectors.
- The server translates Claude Code's streamed events into the stream the page already understands, including Claude's thinking and the exact token counts.
- Claude Code takes one prompt per run, so earlier turns of a chat are sent along with your new message as the conversation so far. Your system prompt replaces Claude Code's own.
- The server only answers requests from the page itself on localhost.
- **Web search** runs on the server. It searches Bing and falls back to Wikipedia, and the results go to Claude with your message. Bing can change its page or block automated searches at any time.
- **Code interpreter** and **custom functions** aren't available with Claude Code yet, because its tools are switched off for safety. Their switches are greyed out.
- **Images** you attach are sent to Claude.
- Chats, templates and settings are saved in this browser's local storage.
- The globe's country outlines are Natural Earth 1:110m (public domain), via world-atlas 2.0.2, simplified and built into the page. Nothing is loaded for them at runtime.

## Old Groq key

The local playground no longer reads `.env`. If you still have a Groq key there and no longer need it, you can delete the file and revoke the key in the GroqCloud console. Never commit `.env` (it's in `.gitignore`).
