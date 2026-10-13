import os
from openai import OpenAI

client = OpenAI(
    api_key="sk-1363adc84c5042a4a50f8b66bc62b76a",
    base_url="https://api.deepseek.com")

response = client.chat.completions.create(
    model="deepseek-flash",
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
    ],
    stream=False,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}}
)

print(response.choices[0].message.content)

#TS not free
# TODO delete