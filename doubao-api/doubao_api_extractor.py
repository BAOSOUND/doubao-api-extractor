#!/usr/bin/env python3
"""
豆包API提取器 - 智能联网版（批量处理版）
基于火山引擎Responses API，支持像网页版一样智能判断的联网搜索
"""

import os
import json
import argparse
import re
import base64
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any
from functools import wraps

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


def retry_on_timeout(max_retries=2, delay=2):
    """超时重试装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay * (attempt + 1))
            return func(*args, **kwargs)
        return wrapper
    return decorator


class DoubaoAPIExtractor:
    """豆包API提取器 - Responses API版（智能联网）"""
    
    def __init__(self, api_key: str = None, endpoint_id: str = None, use_cache: bool = True):
        """
        初始化豆包API提取器
        
        Args:
            api_key: 火山引擎API Key
            endpoint_id: 接入点ID (格式: ep-xxxxxx)
            use_cache: 是否启用缓存
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
        
        # 缓存配置
        self.use_cache = use_cache
        self.cache = {}
        self.cache_ttl = 3600  # 缓存1小时
        
        # 调用统计
        self.stats = {
            "total_calls": 0,
            "cache_hits": 0,
            "total_tokens": 0,
            "total_searches": 0,
            "last_call_time": None
        }
    
    def _get_cache_key(self, question: str, enable_search: bool, max_keyword: int, temperature: float) -> str:
        """生成缓存键"""
        key_data = f"{question}|{enable_search}|{max_keyword}|{temperature}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _enhance_question(self, question: str) -> str:
        """自动为问题添加时效锚点"""
        current_date = datetime.now().strftime("%Y年%m月%d日")
        
        # 检查是否已有时间锚点
        time_keywords = ['今天', '现在', '最新', '最近', '截至', '目前', '当前', '实时']
        if not any(kw in question for kw in time_keywords):
            question = f"截至{current_date}，{question}。请只返回最近的信息，并标注来源。"
        
        return question
    
    @retry_on_timeout(max_retries=2, delay=2)
    def ask(self, 
            question: str,
            system_prompt: str = None,
            enable_search: bool = True,
            max_keyword: int = 1,
            temperature: float = 0.3,
            auto_enhance: bool = True,
            use_caching: bool = True,
            previous_response_id: str = None) -> Dict:
        """
        通用问答方法 - 智能判断是否需要联网
        
        Args:
            question: 用户问题
            system_prompt: 系统提示词（可选）
            enable_search: 是否允许联网搜索
            max_keyword: 单次搜索最大关键词数量
            temperature: 温度参数
            auto_enhance: 是否自动添加时效锚点
            use_caching: 是否使用缓存
            previous_response_id: 上一轮的响应ID（用于多轮对话）
        """
        # 自动优化问题
        if enable_search and auto_enhance:
            question = self._enhance_question(question)
        
        # 检查缓存（仅在不联网时使用）
        if use_caching and self.use_cache and not enable_search:
            cache_key = self._get_cache_key(question, enable_search, max_keyword, temperature)
            if cache_key in self.cache:
                cached_result = self.cache[cache_key]
                # 检查是否过期
                if time.time() - cached_result['timestamp'] < self.cache_ttl:
                    self.stats["cache_hits"] += 1
                    return cached_result['data']
        
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
            "stream": False
        }
        
        # 注意：caching 和 tools 不能同时使用！
        # 只有在不联网时才启用缓存
        if use_caching and not enable_search:
            data["caching"] = {"type": "enabled"}
        
        # 如果允许联网，添加 tools 参数
        if enable_search:
            data["tools"] = [{
                "type": "web_search",
                "max_keyword": max_keyword
            }]
            # 联网时不使用缓存（即使设置了也忽略）
            use_caching = False
        
        # 使用上一轮的响应ID
        if previous_response_id:
            data["previous_response_id"] = previous_response_id
        
        try:
            # 设置合理的超时：连接15秒，读取60秒
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=data,
                timeout=(15, 60)
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
                
                # 从回答文本中二次提取引用（增强）
                text_citations = self._extract_citations_from_text(answer)
                if text_citations and not annotations:
                    annotations = text_citations
                
                final_result = {
                    "content": answer,
                    "success": True,
                    "annotations": annotations,
                    "tool_usage": tool_usage,
                    "searched": search_count > 0,
                    "usage": result.get("usage"),
                    "response_id": result.get("id"),
                    "raw_response": result
                }
                
                # 存入缓存（仅在不联网时缓存）
                if use_caching and self.use_cache and not enable_search:
                    self.cache[cache_key] = {
                        'timestamp': time.time(),
                        'data': final_result
                    }
                
                return final_result
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                return {
                    "content": None,
                    "success": False,
                    "error": error_msg
                }
                
        except requests.exceptions.ConnectTimeout:
            return {
                "content": None,
                "success": False,
                "error": "连接超时，请检查网络后重试"
            }
        except requests.exceptions.ReadTimeout:
            return {
                "content": None,
                "success": False,
                "error": "读取超时，服务响应较慢，请稍后重试"
            }
        except requests.exceptions.ConnectionError as e:
            return {
                "content": None,
                "success": False,
                "error": f"连接错误: {str(e)}"
            }
        except Exception as e:
            return {
                "content": None,
                "success": False,
                "error": str(e)
            }
    
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
    
    def _extract_citations_from_text(self, text: str) -> List[Dict]:
        """从回答文本中提取所有可能的引用"""
        citations = []
        seen_urls = set()
        
        # 模式1: [标题](url)
        md_pattern = r'\[([^\]]+)\]\((https?://[^\s\)]+)\)'
        md_matches = re.findall(md_pattern, text)
        for title, url in md_matches:
            if url not in seen_urls:
                seen_urls.add(url)
                citations.append({
                    "type": "url_citation",
                    "title": title[:100],
                    "url": url,
                    "source": "markdown_link"
                })
        
        # 模式2: [数字](url)
        num_pattern = r'\[(\d+)\]\((https?://[^\s\)]+)\)'
        num_matches = re.findall(num_pattern, text)
        for num, url in num_matches:
            if url not in seen_urls:
                seen_urls.add(url)
                citations.append({
                    "type": "url_citation",
                    "title": f"引用{num}",
                    "url": url,
                    "source": "citation_link"
                })
        
        # 模式3: 纯URL
        url_pattern = r'(https?://[^\s<>"\'()\[\]]+)'
        urls = re.findall(url_pattern, text)
        for url in urls:
            if url not in seen_urls:
                seen_urls.add(url)
                # 尝试从上下文中提取标题
                lines = text.split('\n')
                title = ""
                for i, line in enumerate(lines):
                    if url in line:
                        title = line.replace(url, '').strip()[:50]
                        break
                citations.append({
                    "type": "url_citation",
                    "title": title or f"来源{len(citations)+1}",
                    "url": url,
                    "source": "plain_url"
                })
        
        return citations
    
    def _extract_snippet_for_url(self, answer_text: str, url: str) -> str:
        """
        从回答内容中提取包含该URL的上下文作为摘要
        """
        if not answer_text or not url:
            return ""
        
        # 按句子分割（简单版本）
        sentences = answer_text.replace('。', '。\n').replace('！', '！\n').replace('？', '？\n').split('\n')
        
        # 寻找包含URL的句子
        for sentence in sentences:
            if url in sentence:
                # 返回包含URL的句子作为摘要（限制长度）
                return sentence.strip()[:200]
        
        # 如果没找到包含URL的句子，找包含域名的句子
        domain_match = re.search(r'https?://([^/]+)', url)
        if domain_match:
            domain = domain_match.group(1)
            for sentence in sentences:
                if domain in sentence:
                    return sentence.strip()[:200]
        
        return ""
    
    def deep_search(self, question: str, rounds: int = 2, **kwargs) -> Dict:
        """模拟网页版的深度思考 - 多轮搜索"""
        
        all_answers = []
        all_citations = []
        last_response_id = None
        
        # 第一轮：基础搜索
        result1 = self.ask(question, **kwargs)
        if result1["success"]:
            all_answers.append(result1["content"])
            all_citations.extend(result1.get("annotations", []))
            last_response_id = result1.get("response_id")
            
            # 提取关键实体，优化下一轮搜索
            if rounds >= 2:
                key_entities = self._extract_key_entities(result1["content"])
                if key_entities:
                    refined_q = f"{question}\n重点查询：{', '.join(key_entities[:3])}"
                    result2 = self.ask(
                        refined_q, 
                        previous_response_id=last_response_id,
                        **kwargs
                    )
                    if result2["success"]:
                        all_answers.append(result2["content"])
                        all_citations.extend(result2.get("annotations", []))
        
        # 合并结果
        final_answer = "\n\n---\n\n".join(all_answers)
        
        return {
            "content": final_answer,
            "success": True,
            "annotations": all_citations,
            "searched": True,
            "rounds": len(all_answers)
        }
    
    def _extract_key_entities(self, text: str, max_entities: int = 3) -> List[str]:
        """从文本中提取关键实体（简单实现）"""
        entities = []
        
        # 找中文词语（2-5个字）
        chinese_pattern = r'[\u4e00-\u9fa5]{2,5}'
        found = re.findall(chinese_pattern, text)
        entities.extend(found[:max_entities])
        
        # 去重
        unique_entities = []
        seen = set()
        for e in entities:
            if e not in seen and len(e) > 1:
                seen.add(e)
                unique_entities.append(e)
        
        return unique_entities[:max_entities]
    
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
                "comparison": result["comparison"],
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
    """启动Streamlit界面 - 批量处理版"""
    import streamlit as st
    import pandas as pd
    
    # ===== 页面配置 =====
    st.set_page_config(
        page_title="豆包智能搜索（批量版）",
        page_icon="🥔",
        layout="wide"
    )
    
    # ===== 自定义CSS =====
    st.markdown("""
    <style>
        /* 表格样式 */
        .stDataFrame {
            width: 100%;
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
        
        /* AI回答区域 */
        .answer-box {
            background-color: #f0f2f6;
            padding: 20px;
            border-radius: 10px;
            margin: 10px 0;
            white-space: pre-wrap;
            font-family: inherit;
            line-height: 1.6;
        }
        
        /* 旋转加载动画 */
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .loading-spinner {
            display: inline-block;
            width: 16px;
            height: 16px;
            border: 2px solid #f3f3f3;
            border-top: 2px solid #3498db;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 8px;
            vertical-align: middle;
        }
    </style>
    """, unsafe_allow_html=True)
    
    # ===== 初始化session state =====
    if 'results' not in st.session_state:
        st.session_state.results = []
    if 'processing' not in st.session_state:
        st.session_state.processing = False
    if 'all_citations' not in st.session_state:
        st.session_state.all_citations = []
    
    # ===== 标题 =====
    st.title("🥔 豆包智能搜索 - 批量版")
    st.markdown("> 支持多问题批量处理，自动提取引用来源")
    st.markdown("---")
    
    # ===== 侧边栏 =====
    with st.sidebar:
        # 图标
        icon_path = "blsicon.png"
        if os.path.exists(icon_path):
            with open(icon_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            html_code = f'<img src="data:image/png;base64,{img_data}" width="120" alt="宝宝爆是俺拉" title="宝宝爆是俺拉">'
            st.markdown(html_code, unsafe_allow_html=True)
        else:
            st.markdown("#### 🥔")
        
        st.markdown("---")
        
        # API配置状态
        api_key = os.getenv("DOUBAO_API_KEY")
        endpoint_id = os.getenv("DOUBAO_ENDPOINT_ID")
        
        if not api_key or not endpoint_id:
            st.error("❌ 请先配置 .env 文件")
            st.stop()
        else:
            st.success("✅ API已连接")
        
        # 配置选项
        st.header("⚙️ 搜索配置")
        
        enable_search = st.checkbox("🌐 允许联网搜索", value=True)
        
        col1, col2 = st.columns(2)
        with col1:
            max_keyword = st.number_input("关键词数量", min_value=1, max_value=3, value=1)
        with col2:
            temperature = st.slider("温度", 0.0, 1.0, 0.3, 0.05)
        
        use_cache = st.checkbox("💾 启用缓存", value=True)
        use_deep = st.checkbox("🧠 深度思考", value=False, help="多轮搜索，更深入")
        
        st.markdown("---")
        st.caption("喜欢就分享出去")
    
    # ===== 主界面 - 多问题输入 =====
    st.markdown("### 📝 问题列表")
    st.markdown("**每行一个问题**")
    
    questions_text = st.text_area(
        "问题列表",
        height=150,
        placeholder="例如：\n今天上海天气怎么样？\n2024年AI发展趋势\nPython异步编程的优点",
        label_visibility="collapsed"
    )
    
    questions = [q.strip() for q in questions_text.split('\n') if q.strip()]
    
    if questions:
        st.info(f"📊 共 {len(questions)} 个问题")
        with st.expander("预览问题列表"):
            for i, q in enumerate(questions, 1):
                st.write(f"{i}. {q}")
    
    # ===== 控制按钮 =====
    col1, col2 = st.columns([1, 5])
    with col1:
        start_button = st.button(
            "🚀 开始搜索",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.processing or not questions
        )
    
    # ===== 进度显示 =====
    progress_placeholder = st.empty()
    status_placeholder = st.empty()
    
    # ===== 批量处理函数 =====
    def process_batch():
        """批量处理所有问题"""
        st.session_state.processing = True
        st.session_state.results = []
        st.session_state.all_citations = []
        
        client = DoubaoAPIExtractor(use_cache=use_cache)
        total = len(questions)
        
        for i, question in enumerate(questions):
            # 更新进度
            progress = (i + 1) / total
            progress_placeholder.progress(progress)
            
            status_placeholder.markdown(
                f'<div><span class="loading-spinner"></span><span class="status-text">正在处理第 {i+1}/{total} 个问题...</span></div>',
                unsafe_allow_html=True
            )
            
            # 选择搜索方式
            if use_deep:
                result = client.deep_search(
                    question=question,
                    enable_search=enable_search,
                    max_keyword=max_keyword,
                    temperature=temperature,
                    use_caching=use_cache
                )
            else:
                result = client.ask(
                    question=question,
                    enable_search=enable_search,
                    max_keyword=max_keyword,
                    temperature=temperature,
                    auto_enhance=True,
                    use_caching=use_cache
                )
            
            if result["success"]:
                # 提取引用
                citations = []
                if result.get("annotations"):
                    for ann in result["annotations"]:
                        if ann.get("type") == "url_citation":
                            url = ann.get("url", "")
                            if url:
                                # 获取发布时间
                                publish_time = ""
                                if "publish_time" in ann:
                                    publish_time = ann.get("publish_time", "")
                                elif "publish_time_second" in ann:
                                    pub_time = ann.get("publish_time_second", "")
                                    if pub_time and 'T' in pub_time:
                                        publish_time = pub_time.split('T')[0]
                                    else:
                                        publish_time = pub_time
                                
                                # 从回答中提取摘要
                                snippet = client._extract_snippet_for_url(result["content"], url)
                                
                                citation = {
                                    "序号": len(citations) + 1,
                                    "问题": question,
                                    "网站标题": ann.get("title", f"来源{len(citations)+1}"),
                                    "URL": url,
                                    "发布时间": publish_time,
                                    "摘要": snippet
                                }
                                citations.append(citation)
                                st.session_state.all_citations.append(citation)
                
                # 保存结果
                st.session_state.results.append({
                    "question": question,
                    "answer": result["content"],
                    "citations": citations,
                    "searched": result.get("searched", False)
                })
            else:
                st.session_state.results.append({
                    "question": question,
                    "answer": f"❌ 错误: {result.get('error', '未知错误')}",
                    "citations": [],
                    "searched": False
                })
        
        progress_placeholder.empty()
        status_placeholder.success(f"✅ 完成！共处理 {total} 个问题")
        st.session_state.processing = False
    
    # 执行处理
    if start_button:
        process_batch()
        st.rerun()
    
    # ===== 显示结果 =====
    if st.session_state.results:
        st.markdown("---")
        st.markdown("### 📊 处理结果")
        
        # 统计信息
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("问题总数", len(st.session_state.results))
        with col2:
            success = sum(1 for r in st.session_state.results if "错误" not in r["answer"])
            st.metric("成功", success)
        with col3:
            citations_count = len(st.session_state.all_citations)
            st.metric("引用总数", citations_count)
        
        # 每个问题的详细结果
        for idx, result in enumerate(st.session_state.results):
            with st.expander(f"📌 问题 {idx+1}: {result['question']}", expanded=True):
                # 显示联网状态
                if result.get("searched"):
                    st.info("📡 使用了联网搜索")
                
                # 显示AI回答
                st.markdown("**💬 AI 回答**")
                st.markdown(f'<div class="answer-box">{result["answer"]}</div>', unsafe_allow_html=True)
                
                # 显示引用表格
                if result["citations"]:
                    st.markdown(f"**🔗 引用来源 ({len(result['citations'])} 条)**")
                    df = pd.DataFrame(result["citations"])
                    st.dataframe(
                        df[["序号", "网站标题", "URL", "发布时间", "摘要"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "URL": st.column_config.LinkColumn("URL")
                        }
                    )
                else:
                    st.info("📭 未找到引用来源")
        
        # ===== 下载所有引用 =====
        if st.session_state.all_citations:
            st.markdown("---")
            st.markdown("### 📥 导出引用数据")
            
            df_download = pd.DataFrame(st.session_state.all_citations)
            st.dataframe(
                df_download[["序号", "问题", "网站标题", "URL", "发布时间", "摘要"]].head(10),
                use_container_width=True,
                hide_index=True
            )
            
            csv = df_download.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
            filename = f"doubao_citations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            st.download_button(
                "📥 下载所有引用 (CSV)",
                csv,
                filename,
                "text/csv",
                use_container_width=True
            )
    
    # ===== 底部说明 =====
    st.markdown("---")
    st.caption("💡 批量处理时请耐心等待，联网搜索可能需要30秒左右")


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
            auto_enhance=True,
            use_caching=True
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
