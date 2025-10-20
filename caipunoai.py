#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publisher_dish_fixed_local_image.py

菜谱自动发布脚本（本地封面生成版）
修复与优化：
✅ 文案内容空白问题
✅ 标签格式不符合 MCP 要求
✅ Markdown 符号导致内容被过滤
✅ Qwen 返回结构不统一
✅ 正文自动换行分段，优化阅读体验
✅ 封面图改为本地生成，不再依赖AI模型
"""

import os
import json
import time
import random
import logging
import requests
from datetime import datetime
import pandas as pd
from typing import List, Optional
import re
import hashlib  # 新增用于生成哈希值
import jieba

# 新增 PIL 库导入，用于本地生成图片
from PIL import Image, ImageDraw, ImageFont

# ---------------- 配置区 ----------------
CSV_PATH = "/root/kaoshi/dish_data.csv"
MCP_API_URL = "http://localhost:18060/api/v1/publish"
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

QWEN_TEXT_MODEL = "qwen-plus"
QWEN_IMAGE_MODEL = "qwen-image-plus"  # 尽管不再使用，但保留
MAX_RETRIES = 3
SLEEP_RANGE = (2, 5)
TAGS_COUNT = 3
MAX_TITLE_LENGTH = 20  # 中文字符为单位

# 本地图片配置 - 确保路径为app/images对应实际路径
DISH_IMAGE_DIR = "/root/xiaohongshu-mcp/images"  # 合规的本地文件路径
DISH_FONT_PATH = "/root/xiaohongshu-mcp/汇文明朝体.otf"  # 请确保此路径指向你的字体文件
IMAGE_WIDTH = 1140
IMAGE_HEIGHT = 1472
BG_COLOR = (245, 243, 240)  # 米白色背景
TEXT_COLOR = (24, 125, 62)  # 绿色文字

# 日志配置
logging.basicConfig(
    filename="publisher_dish.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(message)s")
console.setFormatter(formatter)
logging.getLogger().addHandler(console)


# ---------------- 工具函数 ----------------

def truncate_title(title: str) -> str:
    """截断标题"""
    if not title:
        return ""
    title = str(title).strip()
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    return f"{title[:MAX_TITLE_LENGTH-1]}…"


def sanitize_field(text: str) -> str:
    """字段清洗"""
    if not isinstance(text, str):
        return ""
    cleaned = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
    return cleaned


def markdown_to_xiaohongshu(text: str) -> str:
    """
    将 Markdown 文本转换为小红书风格排版：
    - 清理 Markdown 标记
    - 自动分段
    - 保持结构清晰
    """
    if not text:
        return ""

    # 1. 移除常见的 Markdown 标记
    text = re.sub(r'[*_~`]+', '', text)  # 删除强调符号
    text = re.sub(r'^\s*#+\s*', '', text, flags=re.MULTILINE)  # 删除标题标记
    text = re.sub(r'^\s*[-+*]\s+', '• ', text, flags=re.MULTILINE)  # 转换无序列表项为 • 开头
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)  # 删除有序列表编号（可选）

    # 2. 替换多个连续换行为空行（即段落）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 3. 在句末添加换行（中文句号/感叹号/问号）
    text = re.sub(r'([。！？])', r'\1\n', text)

    # 4. 处理粗体：**text** 或 __text__ → text（保留内容）
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)

    # 5. 处理链接：[text](url) → text（只保留文字）
    text = re.sub(r'$$(.*?)$$$(.*?)$$', r'\1', text)

    # 6. 去除首尾空白并返回
    return text.strip()


# ---------------- Qwen 调用 ----------------
def qwen_request(prompt: str, model: str = QWEN_TEXT_MODEL, timeout: int = 60):
    """通用 Qwen 请求函数"""
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
    payload = {"model": model, "input": {"messages": [{"role": "user", "content": prompt}]}}

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    data = resp.json()

    # 改进后的解析，兼容不同返回结构
    text = (
        data.get("output", {}).get("text")
        or data.get("output", {}).get("choices", [{}])[0].get("message", {}).get("content")
        or data.get("output", {}).get("choices", [{}])[0]
        .get("message", {}).get("content", [{}])[0].get("text")
    )

    if isinstance(text, list):
        text = "\n".join([str(t) for t in text])
    return text.strip() if text else None


def call_qwen_title(original_title: str, features: str) -> Optional[str]:
    """AI生成优化标题"""
    prompt = f"""
你是一位商用菜品配方的营销专家，请基于以下信息生成一个适合小红书风格的菜谱标题：
【原始菜名】：{original_title}

要求：
1️⃣ 控制在20个中文字符以内；
2️⃣ 不出现任何品牌、饭店、人物或地名；
3️⃣ 语言自然、有吸引力，强调是菜品的商业配方；
4️⃣ 不要使用引号或标点符号；
5️⃣ 只输出标题。
"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"🎯 生成AI标题: {original_title} (尝试 {attempt})")
            title = qwen_request(prompt)
            if title:
                return truncate_title(title)
        except Exception as e:
            logging.warning(f"❌ 标题生成异常: {e}")
        time.sleep(2)
    return None


def call_qwen_text(dish_name: str, features: str, ingredients: str, process: str) -> Optional[str]:
    """生成小红书风格菜谱文案"""
    prompt = f"""
你是一位专业大厨，请根据以下信息生成一篇“小红书风格”的菜谱分享内容。

【菜名】：{dish_name}
【特点】：{features}
【原料】：{ingredients}
【制作流程】：{process}

写作要求：
1️⃣ 一定要保留原料和制作流程，不能省略；
2️⃣ 强调“这是饭店商用配方”；
3️⃣ 排版要符合小红书阅读习惯，短句和表情符号排版，文风自然、体现商业价值，核心数据加粗，600字左右；
4️⃣ 禁止出现真实饭店、人物或品牌；
5️⃣ 可自然引导收藏或留言（不要违反小红书规定）；
请只输出成文内容。
"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"📝 生成文案: {dish_name} (尝试 {attempt})")
            text = qwen_request(prompt)
            if text:
                return text
        except Exception as e:
            logging.warning(f"❌ 文案生成异常: {e}")
        time.sleep(2)
    return None


def call_qwen_tags(dish_name: str, features: str) -> List[str]:
    """生成标签"""
    prompt = f"""
基于菜名『{dish_name}』和特点『{features}』，生成{TAGS_COUNT}个适合小红书的标签。
要求：
1) 反映菜系/口味/食材/商业效益；
2) 每个标签2~8字；
3) 严格以JSON数组输出，如 ["家常菜","川菜","下饭"]。
"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"🏷️ 生成标签: {dish_name} (尝试 {attempt})")
            tags_str = qwen_request(prompt)
            if tags_str and tags_str.startswith("["):
                try:
                    tags = json.loads(tags_str)
                    tags_clean = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
                    if tags_clean:
                        return tags_clean[:TAGS_COUNT]
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"❌ 标签生成异常: {e}")
        time.sleep(1)
    return [f"{dish_name[:4]}家常", "美食推荐", "下饭菜"]


# ---------------- 封面图 (核心修改) ----------------
def create_dish_image(title: str, dish_name: str) -> Optional[str]:
    """
    本地生成菜谱封面图（优化文字断句，优先按词语拆分）
    :param title: 最终标题
    :param dish_name: 原始菜名
    :return: 本地图片文件路径
    """
    if not os.path.exists(DISH_IMAGE_DIR):
        os.makedirs(DISH_IMAGE_DIR)

    # 生成不含中文和特殊符号的文件名（哈希+时间戳）
    title_hash = hashlib.md5(title.encode('utf-8')).hexdigest()[:8]
    timestamp = int(time.time())
    output_filename = f"dish_{title_hash}_{timestamp}.png"
    output_path = os.path.join(DISH_IMAGE_DIR, output_filename)

    try:
        # 创建图片
        image = Image.new('RGB', (IMAGE_WIDTH, IMAGE_HEIGHT), BG_COLOR)
        draw = ImageDraw.Draw(image)

        # ---------------- 核心优化：智能断句逻辑 ----------------
        title_clean = title.strip()
        if not title_clean:
            logging.error("❌ 标题为空，无法生成图片")
            return None

        # 1. 使用jieba分词拆分词语（精确模式，尽量保留完整词）
        words = jieba.lcut(title_clean, cut_all=False)  # 例如："红烧排骨商用配方" → ["红烧", "排骨", "商用", "配方"]

        # 2. 动态组合词语，每行控制在4-6字（优先完整词语）
        lines = []
        current_line = []
        current_length = 0  # 当前行总字数
        target_min = 2  # 每行最小字数
        target_max = 4  # 每行最大字数

        for word in words:
            word_len = len(word)
            # 如果当前行加该词不超过最大限制，加入当前行
            if current_length + word_len <= target_max:
                current_line.append(word)
                current_length += word_len
            else:
                # 如果当前行已有内容，先收尾当前行
                if current_line:
                    lines.append(''.join(current_line))
                    # 重置当前行，加入新词语
                    current_line = [word]
                    current_length = word_len
                else:
                    # 特殊情况：单个词语超过最大限制（如7字以上），强制按最大字数拆分
                    lines.append(word[:target_max])
                    current_line = [word[target_max:]]
                    current_length = len(current_line[0])

        # 加入最后一行剩余内容
        if current_line:
            lines.append(''.join(current_line))

        # 3. 处理可能的过短行（如最后一行只有1-2字，合并到上一行）
        if len(lines) >= 2 and len(lines[-1]) <= 2:
            lines[-2] += lines[-1]
            lines.pop()

        # ---------------- 字体与排版 ----------------
        margin = 50  # 边距
        max_width = IMAGE_WIDTH - 2 * margin
        max_height = IMAGE_HEIGHT - 2 * margin
        line_count = len(lines)
        
        # 动态计算最佳字体大小（确保文字撑满图片）
        font_size = 10
        best_size = 10
        try:
            while True:
                test_font = ImageFont.truetype(DISH_FONT_PATH, font_size)
                # 检查每行宽度是否合适
                max_line_width = max([draw.textlength(line, font=test_font) for line in lines])
                # 检查总高度是否合适（行高+行间距）
                line_height = test_font.getbbox("国")[3]  # 用典型汉字计算行高
                total_height = line_height * line_count + 20 * (line_count - 1)  # 20px行间距
                
                # 超出边界则停止增大字体
                if max_line_width > max_width or total_height > max_height:
                    break
                best_size = font_size
                font_size += 2  # 逐步增大字体
                
            font = ImageFont.truetype(DISH_FONT_PATH, best_size)
            logging.info(f"📏 最佳字体大小: {best_size}px，行数: {line_count}")
            
        except IOError:
            logging.error(f"❌ 字体文件未找到: {DISH_FONT_PATH}")
            return None

        # 居中绘制文字
        line_height = font.getbbox("国")[3]
        total_text_height = line_height * line_count + 20 * (line_count - 1)
        start_y = (IMAGE_HEIGHT - total_text_height) // 2  # 垂直居中

        for i, line in enumerate(lines):
            line_width = draw.textlength(line, font=font)
            line_x = (IMAGE_WIDTH - line_width) // 2  # 水平居中
            line_y = start_y + i * (line_height + 20)
            draw.text((line_x, line_y), line, fill=TEXT_COLOR, font=font)

        # 保存图片
        image.save(output_path, quality=95)
        logging.info(f"✅ 封面图已保存至: {output_path}，断句结果: {lines}")
        return output_path

    except Exception as e:
        logging.error(f"❌ 图片生成异常: {e}")
        return None


# ---------------- CSV 处理 ----------------
def load_csv_data() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, encoding="utf-8")
    if "已发布" not in df.columns:
        df["已发布"] = "未发布"
    if "发布时间" not in df.columns:
        df["发布时间"] = ""
    for col in ["菜品标题", "特点", "原料", "制作流程"]:
        if col not in df.columns:
            raise ValueError(f"CSV缺少字段: {col}")
        df[col] = df[col].fillna("").astype(str)
    return df


def save_csv_data(df: pd.DataFrame):
    df.to_csv(CSV_PATH, index=False, encoding="utf-8")
    logging.info("💾 数据已保存")


def filter_unpublished(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["已发布"] == "未发布"].reset_index(drop=True)


# ---------------- 发布处理 ----------------
def publish_to_mcp(title: str, content: str, image_path: str, tags: List[str]) -> bool:
    """
    尝试发布内容到 MCP API，提交规范的 /app/images/ 路径格式
    """
    # 提取文件名（从本地绝对路径中分离）
    image_filename = os.path.basename(image_path)
    # 构建规范的应用内路径（/app/images/文件名）
    app_image_path = f"/app/images/{image_filename}"
    
    payload = {
        "title": title.strip(), 
        "content": content.strip(), 
        "images": [app_image_path],  # 使用规范的路径格式
        "tags": tags
    }
    
    logging.info(f"📤 尝试使用规范路径发布: {app_image_path}")
    
    try:
        resp = requests.post(MCP_API_URL, json=payload, timeout=120)
        result = resp.json()
        
        if result.get("success"):
            logging.info(f"✅ 发布成功: {title}")
            return True
            
        logging.error(f"❌ 发布失败，MCP 返回信息: {result}")
        print(f"❌ 发布失败，MCP 返回信息: {result}")
        
    except requests.exceptions.Timeout:
        logging.error("❌ 发布异常: 请求超时")
        print("❌ 发布异常: 请求超时")
    except Exception as e:
        logging.error(f"❌ 发布异常: {e}")
        print(f"❌ 发布异常: {e}")
        
    return False


# ---------------- 主流程 ----------------
def main():
    if not DASHSCOPE_API_KEY:
        print("❌ 未设置 DASHSCOPE_API_KEY 环境变量")
        return

    df = load_csv_data()
    unpublished_df = filter_unpublished(df)
    if unpublished_df.empty:
        print("✅ 没有未发布的数据。")
        return

    # 确保字体文件存在
    if not os.path.exists(DISH_FONT_PATH):
        print(f"❌ 字体文件未找到: {DISH_FONT_PATH}。请检查配置和路径。")
        return

    daily_quota = int(input("\n请输入每日发布数量（建议≤10）: "))
    if daily_quota > len(unpublished_df):
        daily_quota = len(unpublished_df)
    to_publish = unpublished_df.sample(frac=1).reset_index(drop=True)

    for idx in range(daily_quota):
        row = to_publish.iloc[idx]
        original_title = row["菜品标题"]
        features = sanitize_field(row["特点"])
        ingredients = sanitize_field(row["原料"])
        process = sanitize_field(row["制作流程"])

        print(f"\n🧾 [{idx+1}/{daily_quota}] 正在生成AI标题...")
        ai_title = call_qwen_title(original_title, features)
        final_title = ai_title or truncate_title(original_title)
        print(f"✅ 最终标题: {final_title}")

        print("📝 生成文案...")
        content = call_qwen_text(final_title, features, ingredients, process)
        if not content:
            print("❌ 文案生成失败，跳过")
            continue

        # ✅ 关键更新：使用增强型 Markdown 转换函数
        content = markdown_to_xiaohongshu(content)

        print("🏷️ 生成标签...")
        tags = call_qwen_tags(final_title, features)
        tags = [f"#{t}" if not t.startswith("#") else t for t in tags]
        print(f"✅ 标签: {tags}")

        print("🎨 生成封面图...")
        image_path = create_dish_image(final_title, original_title)
        if not image_path:
            print("❌ 图片生成失败，跳过")
            continue

        print(f"\n🚀 发布调试信息：\n标题: {final_title}\n封面(本地): {image_path}\n标签: {tags}\n文案前200字:\n{content[:200]}")

        print("🚀 正在发布...")
        if publish_to_mcp(final_title, content, image_path, tags):
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            df.loc[df["菜品标题"] == original_title, "已发布"] = "已发布" 
            df.loc[df["菜品标题"] == original_title, "发布时间"] = now
            save_csv_data(df)
            print(f"✅ 已发布: {final_title}")
        else:
            print("❌ 发布失败，跳过")

        if idx < daily_quota - 1:
            delay = random.randint(3600 + 300, 3600 + 900)
            print(f"⏳ 下次发布将在 {delay//60} 分钟后...")
            time.sleep(delay)

    print("\n🎉 今日发布完成！")


if __name__ == "__main__":
    main()
