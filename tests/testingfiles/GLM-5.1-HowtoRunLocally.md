---
title: "GLM-5.1 - How to Run Locally"
source: "https://unsloth.ai/docs/models/glm-5.1"
author:
published: 2026-04-07
created: 2026-04-09
description: "Run the new GLM-5.1 model by Z.ai on your own local device!"
tags:
  - "clippings"
---
## zGLM-5.1 - How to Run Locally

Run the new GLM-5.1 model by Z.ai on your own local device!

GLM-5.1 is Z.ai’s new open model. Compared with [GLM-5](https://unsloth.ai/docs/models/tutorials/glm-5), it delivers major improvements in coding, agentic tool use, reasoning, role-play, long-horizon agentic tasks, and overall chat quality.

The full 744B parameter (40B active) GLM-5.1 model has a **200K context** window and requires **1.65TB** of disk space. Unsloth Dynamic 2-bit GGUF reduces the size to **220GB** **(-80%)**, and dynamic **1-bit is 200GB (-85%):** [**GLM-5.1-GGUF**](https://huggingface.co/unsloth/GLM-5.1-GGUF)

All uploads use Unsloth [Dynamic 2.0](https://unsloth.ai/docs/basics/unsloth-dynamic-2.0-ggufs) for SOTA quantization performance - so lower bits has important layers upcasted to 8 or 16-bit. Thank you Z.ai for providing Unsloth with day zero access.

#### ⚙️ Usage Guide

The medium 2-bit dynamic quant `UD-IQ2_M` uses **236GB** of disk space - this can directly fit on a **256GB unified memory Mac** and works well in a **1x24GB GPU** and **256GB of RAM** with MoE offloading. The **1-bit** quant will fit on a 220GB RAM and 8-bit requires 805GB RAM.

For best performance, make sure your total available memory (VRAM + system RAM) exceeds the size of the quantized model file you’re downloading. If it doesn’t, llama.cpp can still run via SSD/HDD offloading, but inference will be slower.

Use distinct settings for different use cases:

Default Settings (Most Tasks)

Terminal Bench

`temperature` = 1.0

`temperature` = 0.7

`top_p` = 0.95

`top_p` = 1.0

max new tokens = 131072

max new tokens = 16384

- **Maximum context window:** `202,752`.
- In GLM-5.1, thinking is enabled by default. To disable thinking:

```
--chat-template-kwargs '{"enable_thinking":false}'
```

#### Chat Template Update

GLM-5.1 adopts the same architecture as GLM-5, just `chat_template.jinja` is different.

- Supports Claude’s search tool. Tools with `defer_loading=True` are omitted from the system prompt and shown in tool results instead.
- Allow empty reasoning blocks (`<think></think>`) in assistant messages. Consecutive assistant messages must remain in the same mode, either thinking or non-thinking.
- Overall, GLM-5.1 mainly improves tool exposure, reasoning-history reconstruction, and tool-message rendering.

## Run GLM-5.1 Tutorials:

You can now run GLM-5.1 in [llama.cpp](https://unsloth.ai/docs/models/glm-5.1#run-in-llama.cpp) and [Unsloth Studio](https://unsloth.ai/docs/models/glm-5.1#run-in-unsloth-studio).

### 🦥 Run in Unsloth Studio

GLM-5.1 can now runs in [Unsloth Studio](https://unsloth.ai/docs/new/studio), our new open-source web UI for local AI. Unsloth Studio lets you run models locally on **MacOS, Windows**, Linux and:

- Search, download, [run GGUFs](https://unsloth.ai/docs/new/studio#run-models-locally) and safetensor models
- [**Self-healing** tool calling](https://unsloth.ai/docs/new/studio#execute-code--heal-tool-calling) + **web search**
- [**Code execution**](https://unsloth.ai/docs/new/studio#run-models-locally) (Python, Bash)
- [Automatic inference](https://unsloth.ai/docs/new/studio#model-arena) parameter tuning (temp, top-p, etc.)
- Uses llama.cpp for Fast CPU + GPU inference and CPU offloading

![](https://unsloth.ai/docs/~gitbook/image?url=https%3A%2F%2F3215535692-files.gitbook.io%2F%7E%2Ffiles%2Fv0%2Fb%2Fgitbook-x-prod.appspot.com%2Fo%2Fspaces%252FxhOjnexMCB3dmuQFQ2Zq%252Fuploads%252FstfdTMsoBMmsbQsgQ1Ma%252Flandscape%2520clip%2520gemma.gif%3Falt%3Dmedia%26token%3Deec5f2f7-b97a-4c1c-ad01-5a041c3e4013&width=768&dpr=3&quality=100&sign=e4b21b2d&sv=2)

1

#### Install Unsloth

Run in your terminal:

**MacOS, Linux, WSL:**

```
curl -fsSL https://unsloth.ai/install.sh | sh
```

**Windows PowerShell:**

```
irm https://unsloth.ai/install.ps1 | iex
```

2

#### Launch Unsloth

**MacOS, Linux, WSL and Windows:**

```
unsloth studio -H 0.0.0.0 -p 8888
```

**Then open** `**http://localhost:8888**` **in your browser.**

3

#### Search and download GLM-5.1

On first launch you will need to create a password to secure your account and sign in again later. You’ll then see a brief onboarding wizard to choose a model, dataset, and basic settings. You can skip it at any time.

You can choose `UD-Q2_K_XL` (dynamic 2bit quant) or other quantized versions like `UD-Q4_K_XL`. We **recommend using our 2bit dynamic quant** `**UD-Q2_K_XL**` **to balance size and accuracy**. If downloads get stuck, see [Hugging Face Hub, XET debugging](https://unsloth.ai/docs/basics/troubleshooting-and-faqs/hugging-face-hub-xet-debugging)

Then go to the [Studio Chat](https://unsloth.ai/docs/new/studio/chat) tab and search for GLM-5.1 in the search bar and download your desired model and quant. It will take some time to download due to the size so please wait. To ensure fast inference, ensure you have [enough RAM/VRAM](https://unsloth.ai/docs/models/glm-5.1#usage-guide), otherwise inference will still work, but Unsloth will offload to your CPU.

![](https://unsloth.ai/docs/~gitbook/image?url=https%3A%2F%2F3215535692-files.gitbook.io%2F%7E%2Ffiles%2Fv0%2Fb%2Fgitbook-x-prod.appspot.com%2Fo%2Fspaces%252FxhOjnexMCB3dmuQFQ2Zq%252Fuploads%252Fkmkcl9FVLkAua8UPLnUz%252FScreenshot%25202026-04-07%2520at%252010.05.26%25E2%2580%25AFAM.png%3Falt%3Dmedia%26token%3D2794e092-a4f2-4209-9b21-1a2410c2631b&width=768&dpr=3&quality=100&sign=10ec20b2&sv=2)

4

#### Run GLM-5.1

Inference parameters should be auto-set when using Unsloth Studio, however you can still change it manually. You can also edit the context length, chat template and other settings.

For more information, you can view our [Unsloth Studio inference guide](https://unsloth.ai/docs/new/studio/chat).

### 🦙 Run in llama.cpp

1

Obtain the latest `llama.cpp` **on** [**GitHub here**](https://github.com/ggml-org/llama.cpp). You can follow the build instructions below as well. Change `-DGGML_CUDA=ON` to `-DGGML_CUDA=OFF` if you don't have a GPU or just want CPU inference. **For Apple Mac / Metal devices**, set `-DGGML_CUDA=OFF` then continue as usual - Metal support is on by default.

```
apt-get update
apt-get install pciutils build-essential cmake curl libcurl4-openssl-dev -y
git clone https://github.com/ggml-org/llama.cpp
cmake llama.cpp -B llama.cpp/build \
    -DBUILD_SHARED_LIBS=OFF -DGGML_CUDA=ON
cmake --build llama.cpp/build --config Release -j --clean-first --target llama-cli llama-mtmd-cli llama-server llama-gguf-split
cp llama.cpp/build/bin/llama-* llama.cpp
```

2

If you want to use `llama.cpp` directly to load models, you can do the below: (:`IQ2_M`) is the quantization type. You can also download via Hugging Face (point 3). This is similar to `ollama run`. Use `export LLAMA_CACHE="folder"` to force `llama.cpp` to save to a specific location. Remember the model has only a maximum of 200K context length.

Follow this for **general instruction** use-cases:

```
export LLAMA_CACHE="unsloth/GLM-5.1-GGUF"
./llama.cpp/llama-cli \
    -hf unsloth/GLM-5.1-GGUF:UD-IQ2_M \
    --ctx-size 16384 \
    --temp 0.7 \
    --top-p 1.0
```

Follow this for **tool-calling** use-cases:

```
export LLAMA_CACHE="unsloth/GLM-5.1-GGUF"
./llama.cpp/llama-cli \
    -hf unsloth/GLM-5.1-GGUF:UD-IQ2_M \
    --ctx-size 16384 \
    --temp 1.0 \
    --top-p 0.95
```

3

Download the model via (after installing `pip install huggingface_hub hf_transfer` ). You can choose `UD-Q2_K_XL` (dynamic 2bit quant) or other quantized versions like `UD-Q4_K_XL`. We **recommend using our 2bit dynamic quant** `**UD-Q2_K_XL**` **to balance size and accuracy**. If downloads get stuck, see [Hugging Face Hub, XET debugging](https://unsloth.ai/docs/basics/troubleshooting-and-faqs/hugging-face-hub-xet-debugging)

```
pip install -U huggingface_hub
hf download unsloth/GLM-5.1-GGUF \
    --local-dir unsloth/GLM-5.1-GGUF \
    --include "*UD-IQ2_M*" # Use "*UD-TQ1_0*" for Dynamic 1bit
```

4

You can edit `--threads 32` for the number of CPU threads, `--ctx-size 16384` for context length, `--n-gpu-layers 2` for GPU offloading on how many layers. Try adjusting it if your GPU goes out of memory. Also remove it if you have CPU only inference.

```
./llama.cpp/llama-cli \
    --model unsloth/GLM-5.1-GGUF/UD-IQ2_M/GLM-5.1-UD-IQ2_M-00001-of-00006.gguf \
    --temp 1.0 \
    --top-p 0.95 \
    --ctx-size 16384 \
    --seed 3407
```

#### 🦙 Llama-server serving & OpenAI's completion library

To deploy GLM-5 for production, we use `llama-server` In a new terminal say via tmux, deploy the model via:

```
./llama.cpp/llama-server \
    --model unsloth/GLM-5.1-GGUF/UD-IQ2_M/GLM-5.1-UD-IQ2_M-00001-of-00006.gguf \
    --alias "unsloth/GLM-5.1" \
    --prio 3 \
    --temp 1.0 \
    --top-p 0.95 \
    --ctx-size 16384 \
    --port 8001
```

Then in a new terminal, after doing `pip install openai`, do:

```
from openai import OpenAI
import json
openai_client = OpenAI(
    base_url = "http://127.0.0.1:8001/v1",
    api_key = "sk-no-key-required",
)
completion = openai_client.chat.completions.create(
    model = "unsloth/GLM-5.1",
    messages = [{"role": "user", "content": "Create a Snake game."},],
)
print(completion.choices[0].message.content)
```

You can then call the served model via the OpenAI API:

```
from openai import AsyncOpenAI, OpenAI
openai_api_key = "EMPTY"
openai_api_base = "http://localhost:8001/v1"
client = OpenAI( # or AsyncOpenAI
    api_key = openai_api_key,
    base_url = openai_api_base,
)
```

### 🔨Tool Calling with GLM-5.1

See [Tool Calling Guide](https://unsloth.ai/docs/basics/tool-calling-guide-for-local-llms) for more details on how to do tool calling. In a new terminal (if using tmux, use CTRL+B+D), we create some tools like adding 2 numbers, executing Python code, executing Linux functions and much more:

```
import json, subprocess, random
from typing import Any
def add_number(a: float | str, b: float | str) -> float:
    return float(a) + float(b)
def multiply_number(a: float | str, b: float | str) -> float:
    return float(a) * float(b)
def substract_number(a: float | str, b: float | str) -> float:
    return float(a) - float(b)
def write_a_story() -> str:
    return random.choice([
        "A long time ago in a galaxy far far away...",
        "There were 2 friends who loved sloths and code...",
        "The world was ending because every sloth evolved to have superhuman intelligence...",
        "Unbeknownst to one friend, the other accidentally coded a program to evolve sloths...",
    ])
def terminal(command: str) -> str:
    if "rm" in command or "sudo" in command or "dd" in command or "chmod" in command:
        msg = "Cannot execute 'rm, sudo, dd, chmod' commands since they are dangerous"
        print(msg); return msg
    print(f"Executing terminal command \`{command}\`")
    try:
        return str(subprocess.run(command, capture_output = True, text = True, shell = True, check = True).stdout)
    except subprocess.CalledProcessError as e:
        return f"Command failed: {e.stderr}"
def python(code: str) -> str:
    data = {}
    exec(code, data)
    del data["__builtins__"]
    return str(data)
MAP_FN = {
    "add_number": add_number,
    "multiply_number": multiply_number,
    "substract_number": substract_number,
    "write_a_story": write_a_story,
    "terminal": terminal,
    "python": python,
}
tools = [
    {
        "type": "function",
        "function": {
            "name": "add_number",
            "description": "Add two numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "string",
                        "description": "The first number.",
                    },
                    "b": {
                        "type": "string",
                        "description": "The second number.",
                    },
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multiply_number",
            "description": "Multiply two numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "string",
                        "description": "The first number.",
                    },
                    "b": {
                        "type": "string",
                        "description": "The second number.",
                    },
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "substract_number",
            "description": "Substract two numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "string",
                        "description": "The first number.",
                    },
                    "b": {
                        "type": "string",
                        "description": "The second number.",
                    },
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_a_story",
            "description": "Writes a random story.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Perform operations from the terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command you wish to launch, e.g \`ls\`, \`rm\`, ...",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Call a Python interpreter with some Python code that will be ran.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to run",
                    },
                },
                "required": ["code"],
            },
        },
    },
]
```

We then use the below functions (copy and paste and execute) which will parse the function calls automatically and call the OpenAI endpoint for any model:

```
from openai import OpenAI
def unsloth_inference(
    messages,
    temperature = 1.0,
    top_p = 0.95,
    top_k = -1,
    min_p = 0.01,
    repetition_penalty = 1.0,
):
    messages = messages.copy()
    openai_client = OpenAI(
        base_url = "http://127.0.0.1:8001/v1",
        api_key = "sk-no-key-required",
    )
    model_name = next(iter(openai_client.models.list())).id
    print(f"Using model = {model_name}")
    has_tool_calls = True
    original_messages_len = len(messages)
    while has_tool_calls:
        print(f"Current messages = {messages}")
        response = openai_client.chat.completions.create(
            model = model_name,
            messages = messages,
            temperature = temperature,
            top_p = top_p,
            tools = tools if tools else None,
            tool_choice = "auto" if tools else None,
            extra_body = {"top_k": top_k, "min_p": min_p, "repetition_penalty" :repetition_penalty,}
        )
        tool_calls = response.choices[0].message.tool_calls or []
        content = response.choices[0].message.content or ""
        tool_calls_dict = [tc.to_dict() for tc in tool_calls] if tool_calls else tool_calls
        messages.append({"role": "assistant", "tool_calls": tool_calls_dict, "content": content,})
        for tool_call in tool_calls:
            fx, args, _id = tool_call.function.name, tool_call.function.arguments, tool_call.id
            out = MAP_FN[fx](**json.loads(args))
            messages.append({"role": "tool", "tool_call_id": _id, "name": fx, "content": str(out),})
        else:
            has_tool_calls = False
    return messages
```

After launching GLM 5.1 via `llama-server` like in [GLM-5.1](https://unsloth.ai/docs/models/glm-5.1#deploy-with-llama-server-and-openais-completion-library) or see [Tool Calling Guide](https://unsloth.ai/docs/basics/tool-calling-guide-for-local-llms) for more details, we then can do some tool calls.

### 📊 Benchmarks

You can view further below for GLM-5.1 benchmarks in table format:

![](https://unsloth.ai/docs/~gitbook/image?url=https%3A%2F%2F3215535692-files.gitbook.io%2F%7E%2Ffiles%2Fv0%2Fb%2Fgitbook-x-prod.appspot.com%2Fo%2Fspaces%252FxhOjnexMCB3dmuQFQ2Zq%252Fuploads%252FJx4pDC6fWJwaQvk1N8X3%252Fbench_51.png%3Falt%3Dmedia%26token%3Da6d51e6e-4e60-43d3-95de-fd3918fbcf67&width=768&dpr=3&quality=100&sign=584e12da&sv=2) ![](https://unsloth.ai/docs/~gitbook/image?url=https%3A%2F%2F3215535692-files.gitbook.io%2F%7E%2Ffiles%2Fv0%2Fb%2Fgitbook-x-prod.appspot.com%2Fo%2Fspaces%252FxhOjnexMCB3dmuQFQ2Zq%252Fuploads%252FvgdCMB73JM8P1OGUDrsJ%252FHFUGDWhW8AAUCbw.jpg%3Falt%3Dmedia%26token%3Dbef138cf-8afb-4e33-8a74-f4695a8cfe45&width=768&dpr=3&quality=100&sign=612ffdeb&sv=2)

Benchmark

GLM-5.1

GLM-5

Qwen3.6-Plus

Minimax M2.7

DeepSeek-V3.2

Kimi K2.5

Claude Opus 4.6

Gemini 3.1 Pro

GPT-5.4

HLE

31.0

30.5

28.8

28.0

25.1

31.5

36.7

**45.0**

39.8

HLE (w/ Tools)

52.3

50.4

50.6

\-

40.8

51.8

**53.1** \*

51.4\*

52.1\*

AIME 2026

95.3

95.4

95.1

89.8

95.1

94.5

95.6

98.2

**98.7**

HMMT Nov. 2025

94.0

**96.9**

94.6

81.0

90.2

91.1

96.3

94.8

95.8

HMMT Feb. 2026

82.6

82.8

87.8

72.7

79.9

81.3

84.3

87.3

**91.8**

IMOAnswerBench

83.8

82.5

83.8

66.3

78.3

81.8

75.3

81.0

**91.4**

GPQA-Diamond

86.2

86.0

90.4

87.0

82.4

87.6

91.3

**94.3**

92.0

SWE-Bench Pro

**58.4**

55.1

56.6

56.2

\-

53.8

57.3

54.2

57.7

NL2Repo

42.7

35.9

37.9

39.8

\-

32.0

**49.8**

33.4

41.3

Terminal-Bench 2.0 (Terminus-2)

63.5

56.2

61.6

\-

39.3

50.8

65.4

**68.5**

\-

Terminal-Bench 2.0 (Best self-reported)

66.5 (Claude Code)

56.2 (Claude Code)

\-

57.0 (Claude Code)

46.4 (Claude Code)

\-

\-

\-

**75.1** (Codex)

CyberGym

**68.7**

48.3

\-

\-

17.3

41.3

66.6

\-

\-

BrowseComp

**68.0**

62.0

\-

\-

51.4

60.6

\-

\-

\-

BrowseComp (w/ Context Manage)

79.3

75.9

\-

\-

67.6

74.9

84.0

**85.9**

82.7

τ³-Bench

70.6

69.2

70.7

67.6

69.2

66.0

72.4

67.1

**72.9**

MCP-Atlas (Public Set)

71.8

69.2

**74.1**

48.8

62.2

63.8

73.8

69.2

67.2

Tool-Decathlon

40.7

38.0

39.8

46.3

35.2

27.8

47.2

48.8

**54.6**

Vending Bench 2

$5,634.00

$4,432.12

$5,114.87

\-

$1,034.00

$1,198.46

**$8,017.59**

$911.21

$6,144.18

Last updated