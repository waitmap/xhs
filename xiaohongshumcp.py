#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import random
import logging
import requests
import csv
from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict, Optional

# 配置区
STATE_FILE = "publish_state.json"
LOG_FILE = "publisher.log"
MCP_API_URL = "http://localhost:18060/api/v1/publish"
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
CSV_PATH = "/root/kaoshi/cleaned_jingshibang_resource.csv"

QWEN_TEXT_MODEL = "qwen-plus"
QWEN_IMAGE_MODEL = "qwen-image-plus"
MAX_RETRIES = 3  # 调用最大重试次数
SLEEP_RANGE = (2, 5)  # 接口调用间隔
TAGS_COUNT = 3  # 生成标签数量

# 年级分组配置
GRADE_GROUPS = {
    "高中组": ["高一", "高二", "高三", "高中", "高考"],
    "初中组": ["初一", "初二", "初三", "中考真"]
}

# 日志配置
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(message)s")
console.setFormatter(formatter)
logging.getLogger().addHandler(console)


def call_qwen_text(title: str) -> Optional[str]:
    """调用Qwen生成文本内容"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"📝 生成文案: {title} (尝试 {attempt})")
            url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DASHSCOPE_API_KEY}"
            }
            payload = {
                "model": QWEN_TEXT_MODEL,
                "input": {
                    "messages": [
                        {"role": "user",
                         "content": f"帮我写一篇小红书风格的笔记，我们要软性介绍{title}这个资料，要客观的角度说它好用，讲价值，吸引家长或学生的兴趣，不要杜撰任何非真实的信息（包括内容、反馈、评论）。要求简洁、有吸引力，带emotion，约120-200字，结尾带CTA（自然地引导用户互动关注/点赞/评论，不要违反小红书的规定）"}
                    ]
                }
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            data = resp.json()
            text = data.get("output", {}).get("text")
            if text:
                return text.strip()
            logging.warning(f"⚠️ 文本生成空结果: {data}")
        except Exception as e:
            logging.warning(f"❌ 文本生成异常: {e}")
        time.sleep(3)
    return None


def call_qwen_tags(title: str) -> List[str]:
    """调用Qwen生成标签（严格遵循MCP格式）"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"🏷️ 生成标签: {title} (尝试 {attempt})")
            url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DASHSCOPE_API_KEY}"
            }
            payload = {
                "model": QWEN_TEXT_MODEL,
                "input": {
                    "messages": [
                        {"role": "user",
                         "content": f"""基于标题『{title}』生成适用于小红书的标签，需满足：
1. 标签与资料内容强相关，强调地域和真题，吸引家长和学生
2. 共生成{TAGS_COUNT}个，每个标签2-8字
3. 不使用特殊符号，纯文字
4. 格式必须为JSON数组（例如：["标签1", "标签2", "标签3"]）
5. 标签间不重复，无空值"""}
                    ]
                }
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            data = resp.json()
            tags_str = data.get("output", {}).get("text", "").strip()
            
            # 严格验证JSON格式
            if tags_str.startswith("[") and tags_str.endswith("]"):
                tags = json.loads(tags_str)
                # 二次清洗确保符合MCP要求
                tags_clean = [str(t).strip() for t in tags if isinstance(t, str) and str(t).strip()]
                if len(tags_clean) >= 1:
                    return tags_clean[:TAGS_COUNT]  # 确保不超过指定数量
            
            logging.warning(f"⚠️ 标签格式不符合要求: {tags_str}")
        except Exception as e:
            logging.warning(f"❌ 标签生成异常: {e}")
        time.sleep(3)
    # 生成失败时使用默认标签
    return [f"{title[:5]}北京真题", "北京初高中资源", "北京家长必备"]


def call_qwen_image(title: str) -> Optional[str]:
    """调用Qwen生成图片"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"🎨 生成封面图: {title} (尝试 {attempt})")
            url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DASHSCOPE_API_KEY}"
            }
            payload = {
                "model": QWEN_IMAGE_MODEL,
                "input": {
                    "messages": [
                        {"role": "user",
                         "content": [{"text": f"生成社交媒体风格的社交媒体封面图，淡粉色背景，上面写着醒目的『{title}』，排版要可爱精致，风格优雅，竖版封面"}]}
                    ]
                },
                "parameters": {
                    "prompt_extend": True,
                    "watermark": False,
                    "size": "1140*1472"
                }
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            data = resp.json()
            image_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
            if image_url:
                return image_url
        except Exception as e:
            logging.warning(f"❌ 图片生成异常: {e}")
        time.sleep(5)
    return None


def publish_to_mcp(title: str, content: str, image_url: str, tags: List[str]) -> bool:
    """发布到MCP接口"""
    if not all([title, content, image_url, tags]):
        logging.error("❌ 存在空值，无法发布")
        return False

    payload = {
        "title": title.strip(),
        "content": content.strip(),
        "images": [image_url],
        "tags": tags  # 直接使用生成的清洗后标签
    }

    try:
        logging.info(f"🚀 发布内容: {json.dumps(payload, ensure_ascii=False)}")
        resp = requests.post(MCP_API_URL, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("success"):
            logging.info(f"✅ 发布成功: {result['message']}")
            return True
        logging.error(f"❌ 发布失败: {result.get('message', '未知错误')}")
    except Exception as e:
        logging.error(f"❌ 发布请求异常: {e}")
    return False


def load_csv_data() -> pd.DataFrame:
    """加载CSV数据并确保必要的列存在"""
    try:
        # 读取CSV文件，确保编码为utf-8
        df = pd.read_csv(CSV_PATH, encoding="utf-8")
        
        # 检查并添加必要的列
        if "已发布" not in df.columns:
            df["已发布"] = "未发布"
        if "发布时间" not in df.columns:
            df["发布时间"] = ""
            
        # 确保年级列存在
        if "年级" not in df.columns:
            logging.error("❌ CSV文件中未找到'年级'列")
            raise ValueError("CSV文件缺少'年级'列")
            
        # 确保标题列存在
        if "title" not in df.columns:
            logging.error("❌ CSV文件中未找到'title'列")
            raise ValueError("CSV文件缺少'title'列")
            
        return df
    except Exception as e:
        logging.error(f"❌ 加载CSV数据失败: {e}")
        raise


def save_csv_data(df: pd.DataFrame):
    """保存数据到CSV文件"""
    try:
        df.to_csv(CSV_PATH, index=False, encoding="utf-8")
        logging.info("💾 数据已成功保存到CSV文件")
    except Exception as e:
        logging.error(f"❌ 保存CSV数据失败: {e}")
        raise


def get_available_grades(df: pd.DataFrame) -> List[str]:
    """获取CSV中所有可用的年级"""
    return sorted(df["年级"].dropna().unique().tolist())


def filter_by_grade(df: pd.DataFrame, grade_choice: str) -> pd.DataFrame:
    """根据年级选择筛选数据"""
    # 筛选未发布的数据
    unpublished = df[df["已发布"] == "未发布"]
    
    if grade_choice in GRADE_GROUPS:
        # 按分组筛选
        target_grades = GRADE_GROUPS[grade_choice]
        return unpublished[unpublished["年级"].isin(target_grades)]
    else:
        # 按具体年级筛选
        return unpublished[unpublished["年级"] == grade_choice]


def show_progress(current: int, total: int, daily_quota: int):
    """显示发布进度"""
    percentage = (current / daily_quota) * 100 if daily_quota > 0 else 0
    print(f"\n📊 今日发布进度: {current}/{daily_quota} ({percentage:.1f}%)")
    print(f"⏳ 剩余待发布: {max(0, daily_quota - current)}条")


def main():
    if not DASHSCOPE_API_KEY:
        logging.error("❌ 未设置DASHSCOPE_API_KEY环境变量")
        print("❌ 错误: 请先设置DASHSCOPE_API_KEY环境变量")
        return

    try:
        # 加载CSV数据
        print("📂 正在加载数据...")
        df = load_csv_data()
        print(f"✅ 成功加载数据，共{len(df)}条记录，其中未发布{len(df[df['已发布'] == '未发布'])}条")

        # 显示年级筛选选项
        available_grades = get_available_grades(df)
        print("\n📋 请选择发布范围:")
        print("0: 高中组 (高一、高二、高三、高中、高考)")
        print("1: 初中组 (初一、初二、初三、中考真)")
        
        for i, grade in enumerate(available_grades, 2):
            print(f"{i}: 具体年级 - {grade}")

        # 获取用户选择
        while True:
            try:
                choice = int(input("\n请输入选项编号: "))
                if choice == 0:
                    selected_grade = "高中组"
                    break
                elif choice == 1:
                    selected_grade = "初中组"
                    break
                elif 2 <= choice < 2 + len(available_grades):
                    selected_grade = available_grades[choice - 2]
                    break
                else:
                    print("❌ 无效选项，请重新输入")
            except ValueError:
                print("❌ 请输入有效的数字")

        # 筛选符合条件的未发布数据
        filtered_df = filter_by_grade(df, selected_grade)
        if len(filtered_df) == 0:
            print(f"❌ 没有找到{selected_grade}的未发布数据")
            return
        print(f"✅ 筛选出{selected_grade}的未发布数据共{len(filtered_df)}条")

        # 设置每日发布数量
        while True:
            try:
                daily_quota = int(input("\n请设置每日发布数量 (建议不超过10条): "))
                if daily_quota > 0 and daily_quota <= len(filtered_df):
                    break
                elif daily_quota > len(filtered_df):
                    print(f"⚠️ 发布数量超过可用数据，已自动调整为{len(filtered_df)}")
                    daily_quota = len(filtered_df)
                    break
                else:
                    print("❌ 请输入大于0的数字")
            except ValueError:
                print("❌ 请输入有效的数字")

        # 打乱顺序，避免固定顺序发布
        to_publish = filtered_df.sample(frac=1).reset_index(drop=True)
        published_count = 0

        # 开始发布流程
        print("\n🚀 开始发布流程...")
        print(f"⏰ 发布计划: 共{daily_quota}条，首次立即发布，后续每小时1条(间隔5-15分钟随机波动)")

        while published_count < daily_quota:
            # 获取当前要发布的记录
            current_idx = to_publish.index[published_count]
            current_row = to_publish.iloc[published_count]
            title = current_row["title"]
            
            print(f"\n📝 正在处理第{published_count + 1}条: {title}")

            # 生成内容
            print("🔤 正在生成文案...")
            content = call_qwen_text(title)
            if not content:
                print("❌ 无法生成文本内容，跳过该条")
                published_count += 1
                continue
            print("✅ 文案生成完成")

            time.sleep(random.randint(*SLEEP_RANGE))
            
            # 生成标签
            print("🏷️ 正在生成标签...")
            tags = call_qwen_tags(title)
            print(f"✅ 生成标签: {tags}")

            time.sleep(random.randint(*SLEEP_RANGE))
            
            # 生成图片
            print("🎨 正在生成封面图...")
            image_url = call_qwen_image(title)
            if not image_url:
                print("❌ 无法生成图片，跳过该条")
                published_count += 1
                continue
            print("✅ 封面图生成完成")

            # 发布到MCP
            print("🚀 正在发布到平台...")
            success = publish_to_mcp(title, content, image_url, tags)
            
            if success:
                # 更新发布状态
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                df.loc[df["title"] == title, "已发布"] = "已发布"
                df.loc[df["title"] == title, "发布时间"] = now
                save_csv_data(df)
                published_count += 1
                print(f"✅ 第{published_count}条发布成功，时间: {now}")
            else:
                print("❌ 发布失败，跳过该条")

            # 显示进度
            show_progress(published_count, daily_quota, daily_quota)

            # 如果不是最后一条，计算下一次发布时间
            if published_count < daily_quota:
                base_interval = 3600  # 1小时(秒)
                random_offset = random.randint(300, 900)  # 5-15分钟(秒)
                next_interval = base_interval + random_offset
                
                # 转换为更易读的格式
                hours = next_interval // 3600
                minutes = (next_interval % 3600) // 60
                print(f"\n⏰ 下一条将在{hours}小时{minutes}分钟后发布")
                time.sleep(next_interval)

        print("\n🎉 今日发布任务已完成!")

    except Exception as e:
        logging.error(f"❌ 程序运行出错: {e}")
        print(f"❌ 程序出错: {e}")


if __name__ == "__main__":
    main()