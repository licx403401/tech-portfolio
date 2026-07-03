#!/usr/bin/env python3
"""
generate.py — 将 data_202606.json 嵌入到 template.html 中，生成最终的 index.html

用法: python generate.py
输出: output/index.html
"""
import json, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE_DIR, "template.html")
DATA_FILE = os.path.join(BASE_DIR, "data_202606.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "index.html")

# 读取数据
with open(DATA_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

# 读取模板
with open(TEMPLATE, "r", encoding="utf-8") as f:
    template = f.read()

# 序列化为 JS 对象（JSON 直接就是合法 JS 字面量）
data_json = json.dumps(data, ensure_ascii=False, indent=2)
replacement = f"  return {data_json};"

# 替换占位符
if "__EMBED_JSON_PLACEHOLDER__" not in template:
    print("❌ 错误：模板中找不到 __EMBED_JSON_PLACEHOLDER__")
    exit(1)

output = template.replace("__EMBED_JSON_PLACEHOLDER__", replacement)

# 写入输出
os.makedirs(OUTPUT_DIR, exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(output)

print(f"✅ 已生成 {OUTPUT_FILE}")
print(f"   数据日期: {data['data_date']}  |  文件大小: {len(output):,} 字节")
