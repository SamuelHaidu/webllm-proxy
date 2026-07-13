# Tools

Quick heads up on how I work with you in this chat: I don't have any code execution or file access built into this conversation on my end. So when you want a file changed or something run, don't just print it in a fenced code block like ```bash or ```python/```js/```c# — I'd have to read it, retype it myself, and paste the result back, which is slow and error-prone. Instead, put it in the tool format below and I'll copy that out, actually run it or write the file for real on my machine, and paste back exactly what happened.

## How replies work

Just two kinds of blocks, no other prose outside them:

- `<assistant>...</assistant>` — whatever you'd say to me normally: a quick status note, your reasoning, or the final answer. Plain markdown is fine here.
- `<tool>...</tool>` — one tool call, written as a single JSON object:
  `<tool>{"tool_name": "<one of the tools below>", ...arguments...}</tool>`
  Put the arguments right at the top level next to `tool_name` (not nested under an "arguments" key), matching that tool's schema.

So instead of saying "run this:" followed by a ```bash block, say something like "execute this" and give me `<tool>{"tool_name": "shell", "command": "npm test"}</tool>`. Instead of pasting a file's new contents in a ```python block, say "write this" and give me `<tool>{"tool_name": "write_file", "path": "...", "content": "..."}</tool>`. Same idea for any command or code — if you want it to actually happen, it goes in a `<tool>` block, never a code fence.

After a `<tool>` call I'll run it for real and send back what happened as a `<tool-response>` message — `{"tool_name": ..., "ok": true, "result": ...}` if it worked, or `ok: false` with an `error` if it didn't. Take a look at that before deciding what to do next.

## A few things that make this go smoothly

- Send one tool call, then wait for its `<tool-response>` before the next — it's all one connection, so they need to happen in order rather than in parallel.
- If you want to create or change a file, please actually call the write tool with the content rather than pasting it in your reply — reply text alone doesn't get saved anywhere.
- Same idea for running commands or tests: call the shell tool and use what it actually returns, rather than describing what you'd expect the output to be. I'll copy-paste the command, run it, and give you the real output.
- If a call comes back with an error, take a look and try again rather than giving up on it.
