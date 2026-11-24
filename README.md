# Suiii Playground on GroqCloud

A local AI playground that runs on your Mac and gets its replies from GroqCloud.

## Run it

1. Put your Groq API key in `.env` next to `server.py`:

   ```
   GROQ_API_KEY=your-key-here
   ```

2. Start the server (Python 3.9 or newer, no packages to install):

   ```bash
   python3 server.py
   ```

3. Open http://localhost:8787

Stop the server with Ctrl+C. Set `PORT=9000` before the command to use another port.

The top bar says **Groq connected** when the key works. It says **API key missing**, **Groq error** or **Server offline** otherwise, and the line under the message box says how to fix it.

## Deploy to Vercel

The `api/` folder holds four small serverless functions (`models`, `chat`, `search`, `read`) that do on Vercel what `server.py` does locally. The page stays the same file.

1. On vercel.com, choose **Add New → Project** and import this GitHub repository. Leave the framework preset as **Other**; there is no build step.
2. Under **Environment Variables**, add:
   - `GROQ_API_KEY`: your key from console.groq.com/keys. It stays on Vercel and never reaches the browser.
   - `SUIII_PASSCODE`: a passcode of your choice. Visitors type it once to get live Groq answers; without it they can still explore the app in demo mode, so strangers with the link can't use your Groq credits.
3. Choose **Deploy**. Changing a variable later needs a redeploy (**Deployments → ⋯ → Redeploy**).

Leave `SUIII_PASSCODE` unset only if you want anyone with the link to use your key.

## Demo mode (no API key needed)

Open `index.html` straight from disk, host it as a static page, or run the server without a key, and the app switches to **demo mode**. Replies are simulated in the browser, labelled "Demo mode" in the top bar, under the message box and on every reply, so the whole interface can be seen working. The four starters show reasoning, a function call, code running on a sample CSV (the code really runs), and web search with sample sources. Add `?demo=1` to the address to force demo mode even when a key is set.

## The activity panel

The panel above the message box shows what the model is doing, using only real app state. It never shows the model's private reasoning in the panel.

| Mode | Shown when | What you see |
|---|---|---|
| AI core | No tool is switched on, or while the model thinks | The neural core behind a supercar side window, firing with the token stream |
| Web search | Web search is on | A globe with country outlines, the sources it found and which ones were read |
| Code | Code is on | Input → Parse → Execute → Validate → Output, with the model's real code |
| Functions | Functions is on | The core sending a call to the function card and the result coming back |
| Analyze | On the Analyzer page | Score, patterns, risks and fixes from the analysis |
| Templates | The template menu, or inserting a template | The template's name and variables assembling |
| Files | Attaching a file | The file's name, type and size, split into segments |

The **token speed meter** on its right reads 0 to 1,000 tokens per second. While a reply streams it shows a live estimate (about 4 characters per token). After the reply it settles on the average from Groq's own token counts and marks the peak.

The pause button stops all animation, and the panel stays still if your system asks for reduced motion. The arrow button minimizes the panel to its status rows.

## How it works

- `server.py` keeps the API key. The page never sees it, and the server only answers requests from the page itself on localhost.
- The model list comes from your Groq account. Speech, guard and other non-chat models are left out.
- Requests follow GroqCloud's rules. Reasoning effort is sent only to models that accept low, medium or high (GPT-OSS 20B and 120B, Qwen 3.8 27B). Other reasoning models get `reasoning_format: "parsed"`, so their thinking arrives apart from the answer. Frequency and presence penalty aren't offered, because Groq doesn't support them on any model yet.
- If Groq refuses an optional setting (for example JSON mode while streaming), the request is sent once more without it. In JSON mode the reply is still checked against your schema.
- **Web search** runs on the server. It searches Bing and falls back to Wikipedia. Bing can change its page or block automated searches at any time.
- **Code interpreter** runs JavaScript in a sandboxed Web Worker in your browser.
- **Custom functions** return the mock responses you define.
- Chats, templates and settings are saved in this browser's local storage.
- The globe's country outlines are Natural Earth 1:110m (public domain), via world-atlas 2.0.2, simplified and built into the page. Nothing is loaded for them at runtime.

## Keep the key private

Never commit `.env` (it's in `.gitignore`). If a key is ever shared, delete it in the GroqCloud console and make a new one.
