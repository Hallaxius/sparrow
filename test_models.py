import httpx
import json
import time

BASE = "https://sparrow-production-f734.up.railway.app"
KEY = "5cd38db87a4645964a0c3343509f0d375050bf91382c015c4cdb9d0b9ba4a35c"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

MODELS = [
    "openai/gpt-5-mini", "openai/gpt-4o-mini",
    "x-preview-f-free", "nemotron-3-ultra-free", "hy3-free", "laguna-s-2.1-free", "nemotron-3.5-lightning-free",
    "nvidia/step-3.7-flash", "nvidia/nemotron-nano-12b-v2-vl", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "nvidia/nemotron-nano-9b-v2",
    "tencent/hy3:free", "nvidia/nemotron-3-ultra-550b-a55b:free", "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3.5-lightning:free", "kilo-auto/small", "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/free", "dots-studio/dots-3-note-preview:free", "nvidia/nemotron-3.5-content-safety:free",
    "liquid/lfm-2.5-2.6b:free", "cohere/north-mini-code:free", "poolside/laguna-xs-2.1:free",
    "kilo-auto/free", "meituan/longcat-2.0-free", "stepfun/step-3.7-flash:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "gpt-oss-20b", "Qwen2.5-VL-72B-Instruct", "Qwen3.6-27B", "Mistral-Nemo-Instruct-2407",
    "Qwen3Guard-Gen-8B", "Qwen3-Coder-30B-A3B-Instruct", "Qwen3-32B", "Mistral-7B-Instruct-v0.3",
    "gpt-oss-120b", "Qwen3.5-9B", "Qwen3Guard-Gen-0.6B", "Mistral-Small-3.2-24B-Instruct-2506",
    "Qwen3.5-397B-A17B", "openai/gpt-4o",
    "DeepSeek-V4-Flash-0731", "gemini-3.1-flash-lite", "minimax-m2.7",
    "meta-Llama-3.1-8B-Instruct-Turbo", "mistral-Nemo-Instruct-2407", "codestral-latest", "gpt-oss:20b",
]

ALIASES = {
    "gpt-4o": "a16ce1ab-4e9d-446e-85ec-34974be6091a/nvidia/nemotron-3-super-120b-a12b:free",
    "gpt-4o-mini": "a16ce1ab-4e9d-446e-85ec-34974be6091a/openrouter/free",
    "claude-3.5-sonnet": "a16ce1ab-4e9d-446e-85ec-34974be6091a/nvidia/nemotron-3-ultra-550b-a55b:free",
    "claude-3-haiku": "1321946a-0d1a-4c00-882e-c626e19047e5/hy3-free",
    "deepseek-r1": "f3100559-d247-449a-baa1-5092dc4fcf6c/DeepSeek-V4-Flash-0731",
    "gemini-2.5-flash": "f3100559-d247-449a-baa1-5092dc4fcf6c/gemini-3.1-flash-lite",
    "mistral-small": "c193adf9-0783-40fa-a892-3ad8463a2fb6/Mistral-Small-3.2-24B-Instruct-2506",
}

results = {"success": [], "failed_503": [], "failed_other": [], "timeout": [], "connection_error": []}

def test_model(model_name, display_name=None):
    label = display_name or model_name
    payload = json.dumps({"model": model_name, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3})
    try:
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=HEADERS, content=payload, timeout=20)
        if r.status_code == 200:
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")[:50]
            print(f"  OK  {label} -> {content}")
            results["success"].append(label)
        elif r.status_code == 503:
            try:
                body = r.json()
                err = body.get("error", {}).get("message", "")[:80] if isinstance(body.get("error"), dict) else str(body.get("error", ""))[:80]
            except Exception:
                err = r.text[:80]
            print(f"  503 {label} -> {err}")
            results["failed_503"].append((label, err))
        else:
            err = r.text[:100]
            print(f"  {r.status_code} {label} -> {err}")
            results["failed_other"].append((label, r.status_code, err))
    except httpx.TimeoutException:
        print(f"  TMO {label}")
        results["timeout"].append(label)
    except httpx.ConnectError as e:
        print(f"  CONN {label} -> {e}")
        results["connection_error"].append((label, str(e)[:80]))

print("=== Testing direct models ===")
for m in MODELS:
    test_model(m)

print("\n=== Testing aliases ===")
for alias in ALIASES:
    test_model(alias)

print(f"\n=== SUMMARY ===")
print(f"Success: {len(results['success'])}")
print(f"503 (no routes/circuit breaker): {len(results['failed_503'])}")
print(f"Other errors: {len(results['failed_other'])}")
print(f"Timeouts: {len(results['timeout'])}")
print(f"Connection errors: {len(results['connection_error'])}")

if results['failed_503']:
    print(f"\n--- 503 Models ---")
    for name, err in results['failed_503']:
        print(f"  {name}: {err}")

if results['failed_other']:
    print(f"\n--- Other Errors ---")
    for name, code, err in results['failed_other']:
        print(f"  {name} ({code}): {err}")
