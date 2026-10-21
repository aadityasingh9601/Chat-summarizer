import ollama

response = ollama.chat(
    model="qwen3:0.6b",
    messages=[{"role": "user", "content": "What is the capital of India?"}]
)
print(response["message"]["content"])