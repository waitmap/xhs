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

# 新增 PIL 库导入，用于本地生成图片
from PIL import Image, ImageDraw, ImageFont

# ---------------- 配置区 ----------------
CSV_PATH = "/root/kaoshi/dish_data.csv"
MCP_API_URL = "http://localhost:18060/api/v1/publish"
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

QWEN_TEXT_MODEL = "qwen-plus"
QWEN_IMAGE_MODEL = "qwen-image-plus" # 尽管不再使用，但保留
MAX_RETRIES = 3
SLEEP_RANGE = (2, 5)
TAGS_COUNT = 3
MAX_TITLE_LENGTH = 20  # 中文字符为单位

# 本地图片配置
DISH_IMAGE_DIR = "/root/xiaohongshu-mcp/images"
DISH_FONT_PATH = "/root/xiaohongshu-mcp/汇文明朝体.otf"  # 🚨🚨🚨 请确保此路径指向你的字体文件 🚨🚨🚨
IMAGE_WIDTH = 1140
IMAGE_HEIGHT = 1472
BG_COLOR = (245, 243, 240) # 白色背景
TEXT_COLOR = (24, 125, 62) # 黑色文字

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


def clean_markdown(text: str) -> str:
    """清理 Markdown 符号，防止 MCP 过滤"""
    if not text:
        return ""
    text = re.sub(r'[#\*\-\>_`]+', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def format_content(text: str) -> str:
    """美化菜谱文案换行格式"""
    if not text:
        return ""
    text = text.strip()

    # 在句号、感叹号、问号后加两个换行
    text = re.sub(r'([。！？])', r'\1\n\n', text)

    # 在关键段落标题前增加换行
    keywords = ["原料", "材料", "食材", "配料", "制作流程", "做法", "步骤", "提示", "总结"]
    for kw in keywords:
        text = re.sub(rf'({kw})[:：]', r'\n\n【\1】\n', text)

    # 压缩多余的空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

# 新增：用于本地图片生成时的文本换行
def wrap_text_dish(text, font, max_width, draw):
    """自定义文本换行函数，按宽度限制换行"""
    lines = []
    text_parts = text.split('\n')
    
    for part in text_parts:
        if not part:
            lines.append('')
            continue

        current_line = []
        current_width = 0
        for char in part:
            char_width = draw.textlength(char, font=font)
            if current_width + char_width <= max_width:
                current_line.append(char)
                current_width += char_width
            else:
                lines.append(''.join(current_line))
                current_line = [char]
                current_width = char_width
        if current_line:
            lines.append(''.join(current_line))
    return lines


# ---------------- Qwen 调用 (未修改部分) ----------------
# ... (Qwen 请求相关函数保持不变) ...

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
1️⃣ 一定要保留原料和制作流程，不省略；
2️⃣ 强调“这是饭店配方”；
3️⃣ 文风自然、有食欲，600字左右；
4️⃣ 禁止出现真实饭店、人物或品牌；
5️⃣ 禁止违规引导互动（例如收藏或留言可获取资料之类）；
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

# 🚨 已删除原 call_qwen_image 函数 🚨

def create_dish_image(title: str, dish_name: str) -> Optional[str]:
    """
    本地生成菜谱封面图（模仿小红书封面，突出菜名文字）
    :param title: 最终标题
    :param dish_name: 原始菜名
    :return: 本地图片文件路径
    """
    if not os.path.exists(DISH_IMAGE_DIR):
        os.makedirs(DISH_IMAGE_DIR)

    # 修正后的代码：先清理标题，再使用 f-string 拼接文件名
    # 注意：这里的 'title' 已经是 final_title，用于生成文件名
    cleaned_title = re.sub(r'[^\w\u4e00-\u9fa5]', '', title)
    output_filename = f"{cleaned_title}_{int(time.time())}.png"
    
    output_path = os.path.join(DISH_IMAGE_DIR, output_filename)

    try:
        # 1. 创建图片
        image = Image.new('RGB', (IMAGE_WIDTH, IMAGE_HEIGHT), BG_COLOR)
        draw = ImageDraw.Draw(image)

        # 2. 标题配置
        # 留出上下边距 100 像素
        margin_y = 100
        available_width = IMAGE_WIDTH - 100 # 左右边距 50
        
        # 3. 字体设置和大小调整
        font_size = 120 # 初始字体大小
        font_path = DISH_FONT_PATH
        
        # 尝试加载字体
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logging.error(f"❌ 字体文件未找到或无法加载: {font_path}")
            print(f"❌ 字体文件未找到或无法加载: {font_path}")
            return None

        # 4. 文本换行
        # 绘制文本的宽度占图片宽度的 90%
        lines = wrap_text_dish(title, font, available_width, draw)
        
        # 5. 调整字体大小以适应最多3行
        max_lines = 3
        
        while len(lines) > max_lines and font_size > 40:
             font_size -= 5
             font = ImageFont.truetype(font_path, font_size)
             lines = wrap_text_dish(title, font, available_width, draw)
        
        # 6. 绘制文本
        total_text_height = sum([font.getbbox(line)[3] for line in lines])
        line_spacing = 30
        total_spacing_height = line_spacing * (len(lines) - 1)
        total_height = total_text_height + total_spacing_height

        # 垂直居中
        current_y = (IMAGE_HEIGHT - total_height) // 2
        
        for line in lines:
            line_width = draw.textlength(line, font=font)
            # 水平居中
            line_x = (IMAGE_WIDTH - line_width) // 2
            
            # 绘制文字
            draw.text((line_x, current_y), line, fill=TEXT_COLOR, font=font)
            
            # 更新 Y 坐标
            line_height = font.getbbox("示")[3]
            current_y += line_height + line_spacing

        # 7. 保存图片
        image.save(output_path, quality=95)
        logging.info(f"✅ 封面图已保存至: {output_path}")
        return output_path

    except Exception as e:
        logging.error(f"❌ 本地图片生成异常: {e}")
        return None

# ---------------- CSV ----------------
# ... (CSV 相关函数保持不变) ...

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


# ---------------- 发布 (修改了签名和内部逻辑以适应本地文件) ----------------

# ---------------- 发布 (修改为提交本地路径) ----------------

def publish_to_mcp(title: str, content: str, image_path: str, tags: List[str]) -> bool:
    """
    尝试发布内容到 MCP API，直接提交本地文件绝对路径作为图片地址。
    
    注意：此方式要求 MCP 后端服务器能够访问这个本地路径 (例如：MCP 运行在同一台服务器
          上，并且有权限读取 /root/caipu/dish_images/ 目录)。
    
    :param title: 标题
    :param content: 正文内容
    :param image_path: 本地图片文件的绝对路径
    :param tags: 标签列表
    :return: 发布是否成功
    """
    
    # 🚨 移除所有关于占位 URL 的代码和警告 🚨
    # 核心修改：直接将本地文件绝对路径作为 images 数组的元素
    payload = {
        "title": title.strip(), 
        "content": content.strip(), 
        # 直接使用本地文件绝对路径
        "images": [os.path.abspath(image_path)], 
        "tags": tags
    }
    
    # 记录日志，确认提交的是绝对路径
    logging.info(f"📤 尝试使用本地路径发布: {os.path.abspath(image_path)}")
    
    try:
        # 在发送请求时，由于内容字段可能包含中文，确保 payload 能够正确编码
        resp = requests.post(MCP_API_URL, json=payload, timeout=120)
        result = resp.json()
        
        if result.get("success"):
            logging.info(f"✅ 发布成功: {title}")
            return True
            
        # 如果发布失败，打印出后端返回的错误信息
        logging.error(f"❌ 发布失败，MCP 返回信息: {result}")
        print(f"❌ 发布失败，MCP 返回信息: {result}")
        
    except requests.exceptions.Timeout:
        logging.error("❌ 发布异常: 请求超时")
        print("❌ 发布异常: 请求超时")
    except Exception as e:
        logging.error(f"❌ 发布异常: {e}")
        print(f"❌ 发布异常: {e}")
        
    return False


# ---------------- 主流程 (核心修改) ----------------

def main():
    if not DASHSCOPE_API_KEY:
        print("❌ 未设置 DASHSCOPE_API_KEY 环境变量")
        # 由于还需要 Qwen 生成标题/文案/标签，所以 DASHSCOPE_API_KEY 仍是必需的
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

        content = clean_markdown(content)
        content = format_content(content)

        print("🏷️ 生成标签...")
        tags = call_qwen_tags(final_title, features)
        tags = [f"#{t}" if not t.startswith("#") else t for t in tags]
        print(f"✅ 标签: {tags}")

        # 核心修改：调用本地图片生成函数，返回的是本地路径
        print("🎨 生成封面图...")
        image_path = create_dish_image(final_title, original_title)
        if not image_path:
            print("❌ 图片生成失败，跳过")
            continue

        print(f"\n🚀 发布调试信息：\n标题: {final_title}\n封面(本地): {image_path}\n标签: {tags}\n文案前200字:\n{content[:200]}")

        print("🚀 正在发布...")
        # 传递本地图片路径给发布函数
        if publish_to_mcp(final_title, content, image_path, tags):
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 使用原标题来定位 DataFrame 中的行
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
