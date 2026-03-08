#!/usr/bin/env python3
"""
豆包API提取器 - 智能联网版
基于火山引擎Responses API，支持像网页版一样智能判断的联网搜索
完全复刻deepseek-extractor.py的界面风格
"""

import os
import json
import argparse
import re
import base64
from datetime import datetime
from typing import Dict, List, Optional, Any

try:
    import requests
except ImportError:
    print("正在安装依赖 requests...")
    os.system("pip install requests")
    import requests

try:
    from dotenv import load_dotenv
except ImportError:
    print("正在安装依赖 python-dotenv...")
    os.system("pip install python-dotenv")
    from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class DoubaoAPIExtractor:
    """豆包API提取器 - Responses API版（智能联网）"""
    
    def __init__(self, api_key: str = None, endpoint_id: str = None):
        """
        初始化豆包API提取器
        
        Args:
            api_key: 火山引擎API Key
            endpoint_id: 接入点ID (格式: ep-xxxxxx)
        """
        self.api_key = api_key or os.getenv("DOUBAO_API_KEY")
        self.endpoint_id = endpoint_id or os.getenv("DOUBAO_ENDPOINT_ID")
        
        if not self.api_key:
            raise ValueError("请提供 API Key，可通过参数传入或设置 DOUBAO_API_KEY 环境变量")
        if not self.endpoint_id:
            raise ValueError("请提供接入点ID，可通过参数传入或设置 DOUBAO_ENDPOINT_ID 环境变量")
        
        # Responses API 地址
        self.base_url = "https://ark.cn-beijing.volces.com/api/v3/responses"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # 调用统计
        self.stats = {
            "total_calls": 0,
            "total_tokens": 0,
            "total_searches": 0,
            "last_call_time": None
        }
    
    def ask(self, 
            question: str,
            system_prompt: str = None,
            enable_search: bool = True,
            max_keyword: int = 1,
            stream: bool = False,
            temperature: float = 0.3) -> Dict:
        """
        通用问答方法 - 智能判断是否需要联网
        
        Args:
            question: 用户问题
            system_prompt: 系统提示词（可选）
            enable_search: 是否允许联网搜索（模型会自主判断是否需要）
            max_keyword: 单次搜索最大关键词数量（控制成本）
            stream: 是否流式输出
            temperature: 温度参数
        """
        # 构建 input 数组
        input_messages = []
        if system_prompt:
            input_messages.append({
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}]
            })
        input_messages.append({
            "role": "user",
            "content": [{"type": "input_text", "text": question}]
        })
        
        # 构建请求数据
        data = {
            "model": self.endpoint_id,
            "input": input_messages,
            "temperature": temperature,
            "stream": stream
        }
        
        # 如果允许联网，添加 tools 参数
        if enable_search:
            data["tools"] = [{
                "type": "web_search",
                "max_keyword": max_keyword
            }]
        
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=data,
                timeout=60
            )
            
            self.stats["total_calls"] += 1
            self.stats["last_call_time"] = datetime.now().isoformat()
            
            if response.status_code == 200:
                result = response.json()
                
                # 提取回答内容
                answer = self._extract_answer(result)
                
                # 提取 annotations（引用来源）
                annotations = []
                try:
                    output = result.get("output", [])
                    for item in output:
                        if item.get("type") == "message":
                            for content in item.get("content", []):
                                if content.get("type") == "output_text":
                                    annotations = content.get("annotations", [])
                except Exception:
                    pass
                
                # 统计工具使用情况
                tool_usage = result.get("usage", {}).get("tool_usage", {})
                search_count = tool_usage.get("web_search", 0)
                self.stats["total_searches"] += search_count
                
                # 统计token用量
                if "usage" in result:
                    self.stats["total_tokens"] += result["usage"].get("total_tokens", 0)
                
                return {
                    "content": answer,
                    "success": True,
                    "annotations": annotations,
                    "tool_usage": tool_usage,
                    "searched": search_count > 0,
                    "usage": result.get("usage"),
                    "raw_response": result if stream else None
                }
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                return {
                    "content": None,
                    "success": False,
                    "error": error_msg
                }
                
        except Exception as e:
            return {
                "content": None,
                "success": False,
                "error": str(e)
            }
    
    def ask_stream(self, question: str, system_prompt: str = None, enable_search: bool = True):
        """流式问答生成器 - 用于实时显示思考过程"""
        input_messages = []
        if system_prompt:
            input_messages.append({
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}]
            })
        input_messages.append({
            "role": "user",
            "content": [{"type": "input_text", "text": question}]
        })
        
        data = {
            "model": self.endpoint_id,
            "input": input_messages,
            "stream": True,
            "temperature": 0.3
        }
        
        if enable_search:
            data["tools"] = [{
                "type": "web_search",
                "max_keyword": 1
            }]
        
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=data,
                stream=True,
                timeout=120
            )
            
            if response.status_code == 200:
                for line in response.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            try:
                                chunk = json.loads(line[6:])
                                yield chunk
                            except json.JSONDecodeError:
                                continue
                yield {"type": "response.completed"}
            else:
                yield {"type": "error", "content": f"错误: {response.text}"}
                
        except Exception as e:
            yield {"type": "error", "content": f"异常: {e}"}
    
    def _extract_answer(self, response_data: Dict) -> str:
        """从Responses API返回中提取回答文本"""
        try:
            output = response_data.get("output", [])
            for item in output:
                if item.get("type") == "message":
                    for content in item.get("content", []):
                        if content.get("type") == "output_text":
                            text = content.get("text", "")
                            return text
            return "无法提取回答内容"
        except Exception:
            return str(response_data)
    
    def analyze_brand(self, 
                     brand_name: str, 
                     aspects: List[str] = None,
                     force_search: bool = True) -> Dict:
        """品牌分析 - 会智能判断是否需要联网"""
        if aspects:
            aspects_str = "、".join(aspects)
            prompt = f"请对【{brand_name}】进行深度品牌分析，重点关注：{aspects_str}。请提供最新数据和信息来源。"
        else:
            prompt = f"请对【{brand_name}】进行全面品牌分析，包括市场表现、用户口碑、技术优势、竞品对比等。请提供最新数据和信息来源。"
        
        system_prompt = """你是一个专业的品牌分析专家。你的特点是：
1. 需要最新信息时会自动联网搜索
2. 提供结构化、多维度分析报告
3. 明确标注信息来源
4. 如果信息不足，诚实说明局限性"""
        
        result = self.ask(
            question=prompt,
            system_prompt=system_prompt,
            enable_search=force_search,
            max_keyword=1,
            temperature=0.3
        )
        
        if result["success"]:
            return {
                "brand": brand_name,
                "aspects": aspects,
                "analysis": result["content"],
                "searched": result.get("searched", False),
                "annotations": result.get("annotations", []),
                "tool_usage": result.get("tool_usage"),
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "brand": brand_name,
                "success": False,
                "error": result.get("error")
            }
    
    def extract_references(self, query: str) -> Dict:
        """提取引用来源 - 强制联网获取最新信息"""
        prompt = f"""{query}

请按以下格式回答：
1. 核心答案
2. 信息来源列表（带链接或出处）
3. 每个信息的发布时间"""
        
        result = self.ask(
            question=prompt,
            enable_search=True,
            max_keyword=1,
            temperature=0.3
        )
        
        return {
            "query": query,
            "result": result["content"] if result["success"] else None,
            "success": result["success"],
            "searched": result.get("searched", False),
            "annotations": result.get("annotations", []),
            "error": result.get("error") if not result["success"] else None
        }
    
    def compare_brands(self, brands: List[str], aspects: List[str] = None) -> Dict:
        """品牌对比分析"""
        brands_str = "、".join(brands)
        if aspects:
            aspects_str = "、".join(aspects)
            prompt = f"请对比分析以下品牌：{brands_str}，重点关注：{aspects_str}。用表格展示，并列出信息来源。"
        else:
            prompt = f"请全面对比以下品牌：{brands_str}，包括技术特点、市场表现、用户评价等。用表格展示，并列出信息来源。"
        
        result = self.ask(
            question=prompt,
            enable_search=True,
            max_keyword=1,
            temperature=0.3
        )
        
        if result["success"]:
            return {
                "brands": brands,
                "aspects": aspects,
                "comparison": result["content"],
                "searched": result.get("searched", False),
                "annotations": result.get("annotations", []),
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "brands": brands,
                "success": False,
                "error": result.get("error")
            }
    
    def save_to_file(self, content: str, filename: str = None) -> str:
        """保存结果到文件"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"doubao_result_{timestamp}.txt"
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"✅ 结果已保存到: {filename}")
        return filename
    
    def get_stats(self) -> Dict:
        """获取调用统计"""
        return self.stats


def create_env_file():
    """创建 .env 配置文件"""
    env_content = """# 豆包API配置
DOUBAO_API_KEY=你的API_KEY
DOUBAO_ENDPOINT_ID=你的接入点ID

# 示例（你的成功配置）：
# DOUBAO_API_KEY=b4d34eaf-0439-4653-8c72-c0abd8b0eb25
# DOUBAO_ENDPOINT_ID=ep-20260302143924-9lqzb
"""
    
    with open(".env", "w", encoding='utf-8') as f:
        f.write(env_content)
    
    print("✅ 已创建 .env 配置文件")
    print("⚠️  请编辑 .env 文件，填入你的API Key和接入点ID")


def clean_filename(text, max_length=50):
    """清理文件名中的特殊字符"""
    if not text:
        return "未知查询"
    text = re.sub(r'[<>:"/\\|?*]', '_', text)
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length]
    return text


def run_streamlit():
    """启动Streamlit界面 - 完全复制deepseek-extractor.py风格"""
    import streamlit as st
    import pandas as pd
    
    # ===== 页面配置 =====
    st.set_page_config(
        page_title="豆包智能搜索",
        page_icon="🥔",
        layout="wide"
    )
    
    # ===== 自定义CSS =====
    st.markdown("""
    <style>
        /* 让表格单元格内容自动换行 */
        .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-0"],
        .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-1"],
        .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-2"],
        .stDataFrame div[data-testid="stDataFrameResizable"] div[data-testid="column-header-3"],
        .stDataFrame td {
            white-space: normal !important;
            word-wrap: break-word !important;
            max-width: none !important;
        }
        
        /* 调整列宽比例 */
        div[data-testid="stDataFrameResizable"] div[data-testid="column-header-0"] { width: 5% !important; }  /* 序号 */
        div[data-testid="stDataFrameResizable"] div[data-testid="column-header-1"] { width: 25% !important; } /* 网站标题 */
        div[data-testid="stDataFrameResizable"] div[data-testid="column-header-2"] { width: 50% !important; } /* URL */
        div[data-testid="stDataFrameResizable"] div[data-testid="column-header-3"] { width: 20% !important; } /* 发布时间 */
        
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
        
        /* 保持AI回答的换行格式 */
        .stMarkdown p {
            white-space: pre-wrap !important;
            margin-bottom: 0.5rem !important;
        }
        
        /* 让列表样式更清晰 */
        .stMarkdown ul, .stMarkdown ol {
            margin-top: 0.25rem !important;
            margin-bottom: 0.5rem !important;
            padding-left: 1.5rem !important;
        }
        
        /* info框内的文本样式 */
        .stAlert p {
            white-space: pre-wrap !important;
            margin-bottom: 0.5rem !important;
        }
    </style>
    """, unsafe_allow_html=True)
    
    # ===== 标题 =====
    st.title("🥔 豆包智能搜索")
    st.markdown("---")
    
    # ===== 输入框 =====
    query = st.text_input("🔍 输入你的问题", placeholder="例如：今天上海天气怎么样？")
    
    # ===== 初始化session state =====
    if 'search_result' not in st.session_state:
        st.session_state.search_result = None
    if 'citations' not in st.session_state:
        st.session_state.citations = []
    if 'answer_text' not in st.session_state:
        st.session_state.answer_text = ""
    if 'question' not in st.session_state:
        st.session_state.question = ""
    
    # ===== 侧边栏 =====
    with st.sidebar:
        # ===== 添加图标（blsicon.png）=====
        icon_path = "blsicon.png"
        
        if os.path.exists(icon_path):
            with open(icon_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            
            html_code = f'<img src="data:image/png;base64,{img_data}" width="120" alt="宝宝爆是俺拉" title="宝宝爆是俺拉">'
            st.markdown(html_code, unsafe_allow_html=True)
        else:
            st.markdown("#### 🥔")
        
        st.header("⚙️ 搜索配置")
        
        # API配置状态
        api_key = os.getenv("DOUBAO_API_KEY")
        endpoint_id = os.getenv("DOUBAO_ENDPOINT_ID")
        
        if api_key and endpoint_id:
            st.success("✅ API已连接")
            
            with st.expander("🔧 条件配置"):
                st.markdown(f"**模型：** Doubao2.0 Pro")
                st.markdown(f"**接入点：** Response API")
                
                max_keyword = st.number_input(
                    "📊 搜索关键词数量",
                    min_value=1,
                    max_value=3,
                    value=1,
                    step=1,
                    help="单次搜索的最大关键词数量，越多成本越高但可能更准确"
                )
                
                temperature = st.slider(
                    "🌡️ 温度 (0-1)",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.3,
                    step=0.05,
                    help="越低越稳定，越高越有创造性"
                )
        else:
            st.error("❌ 请配置 .env 文件")
            st.stop()
        
        enable_search = st.checkbox("🌐 允许联网搜索", value=True, 
                                   help="开启后，模型会判断是否需要上网查最新信息")
        
        st.markdown("---")
        st.caption("喜欢就分享出去")
    
    # ===== 主逻辑：搜索按钮 =====
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("🔍 开始搜索", type="primary", use_container_width=True):
            if not query:
                st.warning("请输入问题")
            else:
                st.session_state.search_result = None
                st.session_state.citations = []
                st.session_state.answer_text = ""
                st.session_state.question = query
                
                with st.spinner("🤔 AI思考中..."):
                    client = DoubaoAPIExtractor()
                    
                    result = client.ask(
                        question=query,
                        enable_search=enable_search,
                        max_keyword=max_keyword,
                        temperature=temperature,
                        stream=False
                    )
                    
                    if result["success"]:
                        st.session_state.answer_text = result["content"]
                        
                        citations = []
                        seen_urls = set()
                        
                        if result.get("annotations"):
                            for ann in result["annotations"]:
                                if ann.get("type") == "url_citation":
                                    url = ann.get("url", "")
                                    if url and url not in seen_urls:
                                        seen_urls.add(url)
                                        
                                        publish_time = ""
                                        if "publish_time" in ann:
                                            publish_time = ann.get("publish_time", "")
                                        elif "publish_time_second" in ann:
                                            pub_time = ann.get("publish_time_second", "")
                                            if pub_time and 'T' in pub_time:
                                                publish_time = pub_time.split('T')[0]
                                            else:
                                                publish_time = pub_time
                                        
                                        citations.append({
                                            '序号': len(citations) + 1,
                                            '网站标题': ann.get("title", f"来源 {len(citations) + 1}"),
                                            'URL': url,
                                            '发布时间': publish_time
                                        })
                        
                        if not citations:
                            citation_pattern = r'\[(\d+)\]\((https?://[^\s\)]+)\)'
                            matches = re.findall(citation_pattern, result["content"])
                            
                            for num, url in matches:
                                if url not in seen_urls:
                                    seen_urls.add(url)
                                    title = f"引用 {num}"
                                    lines = result["content"].split('\n')
                                    for i, line in enumerate(lines):
                                        if f"[{num}]" in line or url in line:
                                            if i > 0 and len(lines[i-1].strip()) > 10:
                                                title = lines[i-1].strip()[:50]
                                            break
                                    
                                    citations.append({
                                        '序号': len(citations) + 1,
                                        '网站标题': title,
                                        'URL': url,
                                        '发布时间': ''
                                    })
                            
                            md_link_pattern = r'\[([^\]]+)\]\((https?://[^\s\)]+)\)'
                            md_matches = re.findall(md_link_pattern, result["content"])
                            
                            for title, url in md_matches:
                                if url not in seen_urls and not any(c.get('URL') == url for c in citations):
                                    seen_urls.add(url)
                                    citations.append({
                                        '序号': len(citations) + 1,
                                        '网站标题': title[:50],
                                        'URL': url,
                                        '发布时间': ''
                                    })
                            
                            url_pattern = r'(https?://[^\s<>"{}|\\^`\[\]]+)'
                            url_matches = re.findall(url_pattern, result["content"])
                            
                            for url in url_matches:
                                if url not in seen_urls and not any(c.get('URL') == url for c in citations):
                                    seen_urls.add(url)
                                    citations.append({
                                        '序号': len(citations) + 1,
                                        '网站标题': f'来源 {len(citations) + 1}',
                                        'URL': url,
                                        '发布时间': ''
                                    })
                        
                        st.session_state.citations = citations
                        
                        if result.get("searched"):
                            st.info("📡 本次回答使用了联网搜索")
                        
                        st.session_state.search_result = True
                    else:
                        st.error(f"错误: {result.get('error')}")
    
    with col2:
        if st.button("🗑️ 清空", use_container_width=True):
            st.session_state.search_result = None
            st.session_state.citations = []
            st.session_state.answer_text = ""
            st.session_state.question = ""
            st.rerun()
    
    # ===== 显示结果 =====
    if st.session_state.search_result or st.session_state.answer_text:
        
        if st.session_state.question:
            st.markdown(f"### 🔍 询问词: {st.session_state.question}")
        
        if st.session_state.answer_text:
            st.markdown("---")
            st.subheader("📄 AI 回答")
            
            answer_text = st.session_state.answer_text
            
            with st.container():
                if "1." in answer_text or "2." in answer_text or "•" in answer_text:
                    formatted_text = answer_text.replace('\n', '  \n')
                    st.info(formatted_text)
                else:
                    st.info(answer_text)
        
        if st.session_state.citations:
            st.markdown("---")
            st.subheader(f"🔗 引用来源 (共找到 {len(st.session_state.citations)} 条)")
            
            html_table = "<table style='width:100%; border-collapse: collapse; margin-bottom: 20px;'>"
            html_table += "<tr style='background-color: #f0f2f6;'>"
            html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:5%'>序号</th>"
            html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:25%'>网站标题</th>"
            html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:50%'>URL</th>"
            html_table += "<th style='padding: 12px; text-align: left; border: 1px solid #ddd; width:20%'>发布时间</th>"
            html_table += "</tr>"
            
            for item in st.session_state.citations:
                html_table += "<tr>"
                html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'>{item.get('序号', '')}</td>"
                html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'>{item.get('网站标题', '')}</td>"
                html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'><a href='{item.get('URL', '#')}' target='_blank' class='citation-link'>{item.get('URL', '')}</a></td>"
                html_table += f"<td style='padding: 8px; border: 1px solid #ddd;'>{item.get('发布时间', '')}</td>"
                html_table += "</tr>"
            
            html_table += "</table>"
            st.markdown(html_table, unsafe_allow_html=True)
            
            if st.session_state.citations:
                display_df = pd.DataFrame(st.session_state.citations)
                csv = display_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
                
                clean_title = clean_filename(st.session_state.question if st.session_state.question else "豆包搜索")
                filename = f"豆包_{clean_title}.csv"
                
                st.download_button(
                    "📥 下载引用来源 CSV",
                    csv,
                    filename,
                    "text/csv",
                    key="download_citations"
                )
    
    st.markdown("---")
    st.caption("""
💡 **提示**：
1. 网页版豆包 ≠ API 版豆包，同一个问题，回答很可能不一样
2. 左侧可开关联网搜索，调节关键词数量和温度
3. 模型会根据问题自主判断是否需要联网
4. 引用来源自动从API返回中提取，包含完整标题和发布时间（如提供）
""")


def main_cli():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="豆包API提取器 - 智能联网版")
    parser.add_argument("--setup", action="store_true", help="创建配置文件")
    parser.add_argument("--ask", type=str, help="提问问题（模型自主判断是否联网）")
    parser.add_argument("--brand", type=str, help="分析指定品牌")
    parser.add_argument("--aspects", type=str, nargs="+", help="分析维度")
    parser.add_argument("--compare", type=str, nargs="+", help="对比多个品牌")
    parser.add_argument("--extract", type=str, help="提取引用来源")
    parser.add_argument("--no-search", action="store_true", help="禁用联网搜索")
    parser.add_argument("--save", action="store_true", help="保存结果到文件")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    parser.add_argument("--gui", action="store_true", help="启动图形界面（Streamlit）")
    
    args = parser.parse_args()
    
    if args.gui:
        run_streamlit()
        return
    
    if args.setup:
        create_env_file()
        return
    
    try:
        extractor = DoubaoAPIExtractor()
        print(f"✅ 初始化成功")
        print(f"📌 接入点ID: {extractor.endpoint_id}")
    except ValueError as e:
        print(f"❌ 初始化失败: {e}")
        return
    
    if args.stats:
        print(json.dumps(extractor.get_stats(), indent=2, ensure_ascii=False))
        return
    
    result = None
    
    if args.ask:
        print(f"\n🔍 问题: {args.ask}")
        print("-" * 50)
        
        result = extractor.ask(
            question=args.ask,
            enable_search=not args.no_search,
            max_keyword=1,
            temperature=0.3,
            stream=False
        )
        if result["success"]:
            print(result["content"])
            if result.get("searched"):
                print("\n📡 本次回答使用了联网搜索")
    
    elif args.brand:
        print(f"\n🏷️  品牌分析: {args.brand}")
        if args.aspects:
            print(f"分析维度: {', '.join(args.aspects)}")
        print("-" * 50)
        result = extractor.analyze_brand(args.brand, args.aspects, force_search=not args.no_search)
        if result.get("success", False):
            print(result["analysis"])
            if result.get("searched"):
                print("\n📡 本次分析使用了联网搜索")
    
    elif args.compare:
        print(f"\n🆚 品牌对比: {', '.join(args.compare)}")
        print("-" * 50)
        result = extractor.compare_brands(args.compare, args.aspects)
        if result.get("success", False):
            print(result["comparison"])
    
    elif args.extract:
        print(f"\n📚 提取引用: {args.extract}")
        print("-" * 50)
        result = extractor.extract_references(args.extract)
        if result["success"]:
            print(result["result"])
    
    else:
        parser.print_help()
        return
    
    if args.save and result and result.get("success", False):
        content = result.get("analysis") or result.get("comparison") or result.get("result") or result.get("content")
        if content:
            extractor.save_to_file(content)
    
    if result and result.get("success", False) and result.get("usage"):
        usage = result.get("usage")
        print(f"\n📊 Token用量: {usage.get('total_tokens', 0)}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        main_cli()
    else:
        run_streamlit()