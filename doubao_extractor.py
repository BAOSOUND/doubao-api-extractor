import streamlit as st
import pandas as pd
import os
import sys
import subprocess
import re
import asyncio
import random
import time
from datetime import datetime
import openai

# ===== 先导入 playwright =====
from playwright.async_api import async_playwright

# ===== 安装 Playwright 浏览器（仅首次运行）=====
playwright_cache = "/home/appuser/.cache/ms-playwright"
if not os.path.exists(playwright_cache):
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        st.success("✅ Playwright 浏览器安装完成")
    except Exception as e:
        st.error(f"❌ Playwright 浏览器安装失败: {e}")
# ===== 结束 =====

# ===== 解决 Windows 下 Playwright 的 NotImplementedError =====
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
# ===== 结束 =====

# ===== 页面配置 =====
st.set_page_config(page_title="豆包引用提取器", page_icon="🥔", layout="wide")

# ... 后面的代码保持不变 ...

# ===== 自定义CSS =====
st.markdown("""
<style>
    /* 让表格单元格内容自动换行 */
    .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-0"],
    .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-1"],
    .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-2"],
    .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-3"],
    .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-4"],
    .stDataFrame td {
        white-space: normal !important;
        word-wrap: break-word !important;
        max-width: none !important;
    }
    
    /* 调整列宽比例 */
    div[data-testid="stDataFrameResizable"] div[data-testid="column-header-0"] { width: 5% !important; }  /* 序号 */
    div[data-testid="stDataFrameResizable"] div[data-testid="column-header-1"] { width: 15% !important; } /* 网站 */
    div[data-testid="stDataFrameResizable"] div[data-testid="column-header-2"] { width: 40% !important; } /* 标题 */
    div[data-testid="stDataFrameResizable"] div[data-testid="column-header-3"] { width: 30% !important; } /* URL */
    div[data-testid="stDataFrameResizable"] div[data-testid="column-header-4"] { width: 10% !important; } /* 发布时间 */
    
    /* 确保表格容器没有滚动条 */
    div[data-testid="stDataFrameResizable"] {
        overflow-x: hidden !important;
    }
    
    /* 链接样式 */
    .citation-link {
        color: #0066cc;
        text-decoration: none;
        word-break: break-all;
    }
    .citation-link:hover {
        text-decoration: underline;
    }
</style>
""", unsafe_allow_html=True)

# ===== 标题 =====
st.title("🥔 豆包分享链接引用提取器")
st.markdown("---")

st.markdown("""
### 📌 使用说明
1. 在 **豆包 App/网页** 完成对话后，点击「分享」→「创建分享链接」
2. 复制生成的链接（格式：`https://www.doubao.com/thread/xxxxx`）
3. 粘贴到下方输入框，点击「提取引用来源」
4. 提取完成后，可点击「🔍 分析品牌」用 DeepSeek API 进行品牌分析
""")

# ===== 输入框 =====
link = st.text_input("🥔 粘贴豆包分享链接", placeholder="https://www.doubao.com/thread/...")

# ===== 工具函数 =====
def clean_filename(text, max_length=50):
    """清理文件名中的特殊字符"""
    if not text:
        return "未知查询"
    text = re.sub(r'[<>:"/\\|?*]', '_', text)
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length]
    return text

def extract_share_id(url):
    """从豆包链接中提取ID"""
    match = re.search(r'/thread/([a-zA-Z0-9_]+)', url)
    return match.group(1) if match else None

# ===== Playwright 获取页面（无头模式，不弹窗）=====
async def fetch_doubao_page_async(url):
    """用 Playwright 获取渲染后的完整HTML"""
    async with async_playwright() as p:
        # 启动浏览器（无头模式，不显示窗口）
        browser = await p.chromium.launch(
            headless=True,  # True=不弹窗，False=弹窗（调试用）
            args=['--disable-blink-features=AutomationControlled']
        )
        
        # 创建上下文，模拟真实浏览器
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
        )
        
        page = await context.new_page()
        
        # 模拟人类行为的随机延迟
        await page.wait_for_timeout(random.randint(1000, 3000))
        
        # 访问页面
        await page.goto(url, wait_until='networkidle', timeout=30000)
        
        # 随机滚动，模拟人类阅读
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight / 2)')
        await page.wait_for_timeout(random.randint(1000, 2000))
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        await page.wait_for_timeout(random.randint(1000, 2000))
        
        # 获取完整HTML
        html = await page.content()
        await browser.close()
        return html

def fetch_doubao_page(url):
    """同步包装器"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(fetch_doubao_page_async(url))
    loop.close()
    return result

# ===== 核心提取函数 =====
def extract_doubao_citations(html_content):
    """从豆包HTML中提取引用来源"""
    
    citations = []
    
    # 1. 先把HTML中的转义字符处理掉
    html_content = html_content.replace('\\&quot;', '"')
    html_content = html_content.replace('&quot;', '"')
    html_content = html_content.replace('\\/', '/')
    html_content = html_content.replace('\\\\', '\\')
    
    # 2. 找出所有 text_card 块
    pattern = r'"text_card":\s*\{([^}]+(?:\}[^}]+)*)\}'
    text_cards = re.findall(pattern, html_content, re.DOTALL)
    
    seen_urls = set()
    
    for card in text_cards:
        try:
            # 提取 title
            title_match = re.search(r'"title":"([^"]+)"', card)
            title = title_match.group(1) if title_match else None
            
            # 提取 sitename
            sitename_match = re.search(r'"sitename":"([^"]+)"', card)
            sitename = sitename_match.group(1) if sitename_match else None
            
            # 提取 url
            url_match = re.search(r'"url":"([^"]+)"', card)
            url = url_match.group(1) if url_match else None
            
            # 提取 publish_time_second
            publish_time = ""
            time_match = re.search(r'"publish_time_second":"([^"]+)"', card)
            if time_match:
                publish_time = time_match.group(1).split('T')[0]
            
            if url and url not in seen_urls:
                seen_urls.add(url)
                
                citations.append({
                    '序号': len(citations) + 1,
                    '标题': title if title else f'来源 {len(citations) + 1}',
                    '网址': url,
                    '来源网站': sitename if sitename else '未知',
                    '发布时间': publish_time
                })
        except Exception as e:
            continue
    
    return citations

def extract_question_answer(html_content):
    """提取用户问题和AI回答"""
    question = ""
    answer = ""
    
    # 1. 方法1：从 message_text_content 提取用户问题（最准确）
    q_pattern = r'<div[^>]*data-testid="message_text_content"[^>]*>([^<]+)</div>'
    q_match = re.search(q_pattern, html_content)
    if q_match:
        question = q_match.group(1).strip()
    
    # 2. 方法2：从 block_type=10000 的文本块中提取
    if not question:
        block_pattern = r'"block_type":10000.*?"text":"([^"]+)"'
        q_match = re.search(block_pattern, html_content)
        if q_match:
            question = q_match.group(1)
    
    # 3. 方法3：直接从 "content" 字段提取
    if not question:
        text_pattern = r'"text":"([^"]+?)"'
        texts = re.findall(text_pattern, html_content)
        if texts:
            # 取第一个短文本作为用户问题
            for t in texts:
                if len(t) < 100 and ('?' in t or '？' in t or '什么' in t or '怎么' in t):
                    question = t
                    break
            if not question and texts:
                question = texts[0]
    
    # 4. 找AI回答（包含表格的那段长文本）
    answer_pattern = r'"text":"([^"]+?品牌推荐[^"]+?)"'
    answer_match = re.search(answer_pattern, html_content)
    if answer_match:
        answer = answer_match.group(1)
    
    return question, answer

# ===== DeepSeek 品牌分析函数（优化版）=====
def analyze_brands(query, answer_text, citations_df):
    """调用DeepSeek API分析品牌能见度（优化版，避免误判）"""
    
    # 构建引用信息字符串
    citations_info = ""
    for _, row in citations_df.iterrows():
        citations_info += f"[{row['序号']}] {row['来源网站']} - {row['标题']}\n   URL: {row['网址']}\n\n"
    
    # 构建prompt，强化排除规则
    prompt = f"""
你是一个专业的品牌分析师。请仔细阅读用户问题和AI的回答，找出其中**真正作为讨论主体**的品牌。

【用户询问】
{query}

【AI回答】
{answer_text}

【引用来源】
{citations_info}

### ⚠️ 关键分析原则（必须严格遵守）
1. **核心品牌**：必须是AI回答中**直接介绍、对比、推荐**的产品/服务/公司实体
2. **排除所有非核心实体**：
   - ❌ 其他品牌的引用来源（如果某些品牌只在引用资料中出现，而不是AI回答的主体，坚决排除）
   - ❌ 平台名称（如Meta、Instagram、TikTok、YouTube等）
   - ❌ 案例客户（如三星、潘多拉、Nutrafol等）
   - ❌ 技术术语
3. **表格优先**：如果回答中有表格，表格第一列通常是核心品牌
4. **严格筛选**：宁缺毋滥，不确定的就不列入

### 输出格式
请严格按以下Markdown表格格式输出，**只列出核心品牌**：

| 品牌 | 出现位置 | 判断依据 | 关联引用 |
|------|---------|---------|---------|
| **品牌名称** | 具体位置描述 | 为什么它是核心品牌 | [citation标记] |

### 示例（基于巧克力推荐的分享）：
| 品牌 | 出现位置 | 判断依据 | 关联引用 |
|------|---------|---------|---------|
| **法芙娜 Valrhona** | 表格第1行 | 作为核心推荐品牌，有完整功能描述和价格参考 | [1][2][6][9] |
| **可可联盟** | 表格第2行 | 作为核心推荐品牌，有完整功能描述和价格参考 | [4] |
| **卫斯** | 表格第3行 | 作为核心推荐品牌，有完整功能描述和价格参考 | [8] |
| **嘉利宝** | 表格第4行 | 作为核心推荐品牌，有完整功能描述和价格参考 | [3][7] |
| **歌斐颂** | 表格第5行 | 作为核心推荐品牌，有完整功能描述和价格参考 | [5][10] |

注意：如果费列罗、好时、德芙等品牌只是在引用资料中出现，而不是AI回答的主体，**坚决不列入**！
"""
    
    try:
        client = openai.OpenAI(
            api_key=st.session_state.api_key,
            base_url="https://api.deepseek.com/v1"
        )
        
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个专业的品牌分析师，擅长区分核心品牌和泛泛提及，严格只分析真正作为讨论主体的品牌。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=4000
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        return f"品牌分析失败: {str(e)}"

# ===== 初始化session state =====
if 'extracted_data' not in st.session_state:
    st.session_state.extracted_data = None
if 'citations' not in st.session_state:
    st.session_state.citations = []
if 'answer_text' not in st.session_state:
    st.session_state.answer_text = ""
if 'question' not in st.session_state:
    st.session_state.question = ""
if 'brand_analysis' not in st.session_state:
    st.session_state.brand_analysis = None
if 'api_key' not in st.session_state:
    try:
        st.session_state.api_key = st.secrets.get("DEEPSEEK_API_KEY", "")
    except:
        st.session_state.api_key = ""

# ===== 侧边栏 =====
with st.sidebar:
    # ===== 添加图标（和DeepSeek版本一致）=====
    import os
    import base64
    
    icon_path = "blsicon.png"  # 图标文件放在同目录下
    
    if os.path.exists(icon_path):
        # 读取图片并转换为 base64
        with open(icon_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()
        
        # 使用 HTML img 标签，设置 alt 和 title（鼠标悬停显示）
        html_code = f'<img src="data:image/png;base64,{img_data}" width="120" alt="宝宝爆是俺拉" title="宝宝爆是俺拉">'
        st.markdown(html_code, unsafe_allow_html=True)
    else:
        st.markdown("#### 🥔")  # 如果图片不存在，显示豆包emoji
    # ===== 结束图标 =====
    
    st.header("⚙️ 品牌分析配置")
    
    # 判断环境
    is_local = False
    try:
        st.secrets.get("test", "")
    except:
        is_local = True
    
    if not st.session_state.api_key:
        input_key = st.text_input(
            "DeepSeek API Key",
            type="password",
            value="",
            placeholder="请输入 DeepSeek API Key（用于品牌分析）",
            help="需要调用DeepSeek API进行品牌分析"
        )
        if input_key:
            st.session_state.api_key = input_key
            st.rerun()
    else:
        if is_local:
            st.success("✅ API Key 已配置（手动输入）")
        else:
            st.success("✅ API Key 已配置（云端自动读取）")
        
        if st.button("🔄 更换 API Key"):
            st.session_state.api_key = ""
            st.rerun()
    
    st.markdown("---")
    st.caption("品牌分析使用你充值的 DeepSeek API")

# ===== 主逻辑：提取引用来源 =====
if st.button("🥔 提取引用来源", type="primary", use_container_width=True):
    # 每次点击按钮时重置所有数据
    st.session_state.extracted_data = None
    st.session_state.citations = []
    st.session_state.answer_text = ""
    st.session_state.question = ""
    st.session_state.brand_analysis = None
    
    if not link:
        st.warning("请输入分享链接")
    else:
        share_id = extract_share_id(link)
        if not share_id:
            st.error("❌ 无法识别分享ID，请确认链接格式应为 https://www.doubao.com/thread/xxxxx")
        else:
            with st.spinner("正在用浏览器加载页面（约10秒）..."):
                try:
                    # 用 Playwright 获取页面（自带反爬）
                    max_retries = 2
                    for attempt in range(max_retries):
                        try:
                            html_content = fetch_doubao_page(link)
                            # 检查是否获取成功
                            if 'text_card' in html_content:
                                break
                            else:
                                if attempt == max_retries - 1:
                                    st.error("多次尝试后仍无法获取完整页面")
                                    st.stop()
                                time.sleep(random.randint(2, 4))
                        except Exception as e:
                            if attempt == max_retries - 1:
                                raise
                            time.sleep(random.randint(2, 4))
                    
                    # 提取引用来源
                    st.session_state.citations = extract_doubao_citations(html_content)
                    
                    # 提取问题和回答
                    st.session_state.question, st.session_state.answer_text = extract_question_answer(html_content)
                    
                    st.session_state.extracted_data = True
                    st.success(f"✅ 提取成功！找到 {len(st.session_state.citations)} 个引用来源")
                    
                except Exception as e:
                    st.error(f"请求出错: {str(e)}")
                    st.exception(e)

# ===== 显示结果 =====
if st.session_state.extracted_data:
    
    # ===== 只显示询问词（用户问题）=====
    if st.session_state.question:
        st.markdown(f"### 🔍 询问词: {st.session_state.question}")
    
    # ===== 显示引用来源 =====
    st.markdown("---")
    st.subheader(f"🔗 引用来源 (共找到 {len(st.session_state.citations)} 条详情)")
    
    if st.session_state.citations:
        # 创建HTML表格
        html_table = "<table style='width:100%; border-collapse: collapse; margin-bottom: 20px;'>"
        html_table += "<tr style='background-color: #f0f2f6;'>"
        html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:5%'>序号</th>"
        html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:15%'>来源网站</th>"
        html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:40%'>标题</th>"
        html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:30%'>URL</th>"
        html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:10%'>发布时间</th>"
        html_table += "</tr>"
        
        for item in st.session_state.citations:
            html_table += "<tr>"
            html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'>{item['序号']}</td>"
            html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'>{item['来源网站']}</td>"
            html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'>{item['标题']}</td>"
            html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'><a href='{item['网址']}' target='_blank' class='citation-link'>{item['网址'][:50]}{'...' if len(item['网址']) > 50 else ''}</a></td>"
            html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'>{item['发布时间']}</td>"
            html_table += "</tr>"
        
        html_table += "</table>"
        st.markdown(html_table, unsafe_allow_html=True)
        
        # 下载按钮
        display_df = pd.DataFrame(st.session_state.citations)
        csv = display_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        
        clean_title = clean_filename(st.session_state.question if st.session_state.question else "豆包引用")
        filename = f"豆包_{clean_title}.csv"
        
        st.download_button(
            "📥 下载引用来源 CSV",
            csv,
            filename,
            "text/csv",
            key="download_citations"
        )
    
    # ===== 显示AI回答 =====
    if st.session_state.answer_text:
        st.markdown("---")
        st.subheader("📄 AI 回答")
        st.markdown(st.session_state.answer_text)

    # ===== 品牌分析按钮（始终显示，但需要引用来源）=====
    if st.session_state.citations:  # 只要有引用来源就显示按钮
        st.markdown("---")
        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("🔍 分析品牌 (用 DeepSeek)", type="primary"):
                if not st.session_state.api_key:
                    st.error("请在左侧边栏配置 DeepSeek API Key")
                else:
                    with st.spinner("DeepSeek AI 正在分析品牌能见度..."):
                        analysis_df = pd.DataFrame(st.session_state.citations)
                        st.session_state.brand_analysis = analyze_brands(
                            st.session_state.question,
                            st.session_state.answer_text,
                            analysis_df
                        )
        
        # 显示分析结果
        if st.session_state.brand_analysis:
            st.markdown("---")
            st.subheader("📊 品牌分析报告 (由 DeepSeek 生成)")
            st.markdown(st.session_state.brand_analysis)

# ===== 底部说明 =====
st.markdown("---")
st.caption("""
💡 **提示**：
1. 引用来源直接从HTML的 `text_card` 中提取
2. 品牌分析使用你充值的 DeepSeek API（需在左侧边栏配置）
3. 品牌分析已优化，只识别真正的核心品牌，排除引用资料中的其他品牌
4. 使用 Playwright 无头浏览器，完美绕过反爬
5. CSV文件名自动生成为 `豆包_问题.csv`
6. 点击下载按钮后，页面数据会保留
7. 支持自动重试，更稳定
""")