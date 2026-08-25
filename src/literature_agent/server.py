from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import time
import zipfile
from base64 import b64decode
import binascii
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .attention_pipeline import get_attention_job, list_attention_jobs, start_attention_job
from .chat import (
    chat_with_kimi,
    load_default_model_provider,
    load_provider_api_key,
    mask_api_key,
    normalize_model_provider,
    provider_api_base,
    provider_label,
    save_default_model_provider,
    save_provider_api_base,
    save_provider_api_key,
)
from .config import AppConfig, load_config
from .config_updates import apply_config_update, preview_config_update
from .library_store import (
    get_library_paper_detail,
    get_topic_library,
    library_graph,
    library_stats,
    search_library,
    search_library_topics,
)
from .research_workflow import run_research_workflow
from .search_pipeline import run_search_pipeline
from .storage import list_download_batches, persist_capture
from .survey_reporting import (
    generate_library_survey_report,
    list_survey_reports,
    read_survey_report,
)
from .template_library import (
    download_template,
    get_template,
    list_templates,
    resolve_template_request,
)
from .template_guardrails import (
    analyze_project_template_guardrails,
    load_guardrails,
    read_project_guardrails_yaml,
    resolve_section_id,
    save_project_guardrails_yaml,
)
from .template_profile import build_template_profile, template_comprehension_prompt
from .writing_audit import AuditReport, audit_fix_prompt, run_audit_and_revise, run_full_audit
from .writing_workflow import (
    apply_section_citations,
    compress_context,
    get_exploration_report,
    get_workflow_state,
    lock_chapter,
    negotiate_section,
    recommend_writing_order,
    run_final_review,
    save_section_draft,
    select_exploration_topic,
    set_writing_order,
    start_chapter_writing,
    start_outline_negotiation,
    unlock_chapter,
)
from .writing_workspace import (
    _split_bibtex_entries,
    analyze_chapters_with_llm,
    compile_project,
    create_project,
    create_project_file,
    delete_project,
    import_local_workspace,
    import_project_archive,
    insert_workspace_figure,
    list_projects,
    load_project,
    load_project_context,
    load_project_sources,
    load_workspace_index,
    merge_project_bibliography,
    project_structure_digest,
    read_pdf_bytes,
    read_project_file,
    record_project_turn,
    save_project_sources,
    save_project_file,
    sync_workflow_project,
    update_section_memory,
)


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "ScientificAgentCapture/0.1"

    def _normalize_writing_language(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"en", "english", "en-us", "en-gb"}:
            return "en"
        return "zh"

    def _language_name(self, value: Any) -> str:
        return "English" if self._normalize_writing_language(value) == "en" else "Chinese"

    def _llm_timeout(self, kind: str, default: int) -> int:
        key = f"SCIENTIFIC_AGENT_{kind.upper()}_TIMEOUT"
        raw = os.environ.get(key, "").strip()
        if raw.isdigit():
            return max(30, int(raw))
        return default

    def _llm_retries(self, kind: str, default: int) -> int:
        key = f"SCIENTIFIC_AGENT_{kind.upper()}_RETRIES"
        raw = os.environ.get(key, "").strip()
        if raw.isdigit():
            return max(1, int(raw))
        return default

    def _bibliography_instruction(self, project: dict[str, Any]) -> str:
        profile = project.get("template_profile") or project.get("bibliography_profile") if isinstance(project, dict) else {}
        if not isinstance(profile, dict):
            profile = {}
        # Use the richer bibliography sub-profile from template_profile if available
        bib = profile.get("bibliography") if isinstance(profile.get("bibliography"), dict) else profile
        backend = str(bib.get("backend") or "").strip().lower()
        cite_commands = [str(item).strip() for item in (bib.get("cite_commands") or profile.get("cite_commands") or []) if str(item).strip()]
        bib_files = [str(item).strip() for item in (bib.get("bib_files") or profile.get("bib_files") or []) if str(item).strip()]
        preferred_generics = ["parencite", "citep", "autocite", "smartcite", "footcite", "textcite", "citet"]
        preferred_command = next((item for item in cite_commands if item in preferred_generics), None)
        if not preferred_command:
            if "cite" in cite_commands:
                preferred_command = "cite"
            elif any(c in cite_commands for c in preferred_generics):
                preferred_command = next(c for c in preferred_generics if c in cite_commands)
            else:
                preferred_command = "cite"
        cite_text = ", ".join(f"\\{item}" for item in cite_commands[:6]) if cite_commands else "未检测到"
        bib_text = ", ".join(bib_files[:4]) if bib_files else "未检测到"
        project_mode = str(project.get("project_mode") or profile.get("project_mode") or "").strip()
        if backend == "biblatex":
            return (
                "参考文献系统：biblatex。请沿用模板里的 \\addbibresource / \\printbibliography，"
                "不要改成 \\bibliographystyle / \\bibliography。优先使用模板已有命令（"
                f"{cite_text}），新增引用默认使用 \\{preferred_command}。"
                "不要把原模板里的引用命令重写成标准 \\cite。"
                f"Bib 文件：{bib_text}。"
            )
        if backend in {"natbib", "bibtex"}:
            return (
                f"参考文献系统：{backend}。请沿用模板里的 \\bibliographystyle / \\bibliography，"
                "不要改成 biblatex。优先使用模板已有命令（"
                f"{cite_text}），新增引用默认使用 \\{preferred_command}。"
                "不要把原模板里的引用命令重写成标准 \\cite。"
                f"Bib 文件：{bib_text}。"
            )
        if project_mode == "manual_upload":
            if cite_commands or bib_files:
                return (
                    "这是手动上传模板。已检测到引用命令（"
                    f"{cite_text}）和 Bib 文件（{bib_text}）。新增引用请使用 \\{preferred_command}，"
                    "不要擅自新增一套引用系统，只沿用原模板中的命令和文件。"
                )
            return "这是手动上传模板；如果模板没有明确的参考文献入口，不要擅自新增一套引用系统，只沿用原模板中的命令和文件。"
        if cite_commands or bib_files:
            return f"已检测到引用命令（{cite_text}）和 Bib 文件（{bib_text}）；请沿用模板已有格式，不要自行切换参考文献系统。"
        return "未检测到明确参考文献系统；请沿用模板中已有的引用命令和 .bib 文件，不要擅自改写参考文献格式。"

    def _library_cards_bibtex(self, cards: list[dict[str, Any]]) -> str:
        entries: list[str] = []
        for card in cards:
            key = str(card.get("key") or "").strip()
            if not key:
                continue
            title = str(card.get("title") or "").strip()
            year_match = re.search(r"(19|20)\d{2}", str(card.get("year") or ""))
            year = year_match.group(0) if year_match else ""
            venue = str(card.get("venue") or "").strip()
            fields: list[str] = []
            if title:
                fields.append(f"  title = {{{title}}},")
            if venue:
                fields.append(f"  journal = {{{venue}}},")
            if year:
                fields.append(f"  year = {{{year}}},")
            if not fields:
                continue
            entries.append("@article{" + key + ",\n" + "\n".join(fields) + "\n}")
        return "\n\n".join(entries).strip() + ("\n" if entries else "")

    def _citation_keys_in_tex(self, content: str) -> set[str]:
        text = str(content or "")
        keys: set[str] = set()
        pattern = re.compile(
            r"\\(?:parencite|textcite|autocite|smartcite|footcite|footcitetext|citep|citet|citeauthor|citeyearpar|citeyear|cite)\*?"
            r"(?:\[[^\]]*\]){0,2}\{([^}]+)\}"
        )
        for match in pattern.finditer(text):
            keys.update(item.strip() for item in str(match.group(1) or "").split(",") if item.strip())
        return keys

    def _filter_bibtex_to_used_keys(self, bibliography: str, content: str) -> str:
        used_keys = self._citation_keys_in_tex(content)
        if not used_keys:
            return ""
        entries = _split_bibtex_entries(str(bibliography or "").strip())
        kept: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            match = re.search(r"@\w+\{([^,]+),", entry)
            if not match:
                continue
            key = str(match.group(1) or "").strip()
            if not key or key in seen or key not in used_keys:
                continue
            kept.append(entry.strip())
            seen.add(key)
        return "\n\n".join(kept).strip() + ("\n" if kept else "")

    def _section_id_for_file(self, project_id: str, file_path: str, section_context: dict[str, Any]) -> str:
        try:
            guardrails = load_guardrails(project_id)
        except Exception:
            return ""
        return resolve_section_id(
            guardrails,
            rel_path=file_path,
            title=str(section_context.get("title") or ""),
        )

    def _template_language(self, template_id: str) -> str:
        template_ref = str(template_id or "").strip()
        if not template_ref:
            return ""
        try:
            template = get_template(template_ref)
        except Exception:
            return ""
        return self._normalize_writing_language(template.get("language") or "")

    def _contains_english_request(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return any(
            token in lowered
            for token in [
                "english",
                "英文",
                "ieee",
                "acm",
                "elsevier",
                "lncs",
                "journal paper",
                "conference paper",
                "paper in english",
            ]
        )

    def _infer_writing_language(
        self,
        message: str = "",
        requirements: str = "",
        writing_type: str = "academic",
        template_id: str = "",
        sources: list[dict[str, Any]] | None = None,
        project: dict[str, Any] | None = None,
        explicit: str = "",
    ) -> str:
        if str(explicit or "").strip():
            return self._normalize_writing_language(explicit)
        project_meta = project or {}
        project_language = str(project_meta.get("writing_language") or "").strip()
        if project_language:
            return self._normalize_writing_language(project_language)
        template_language = self._template_language(template_id or str(project_meta.get("template_id") or ""))
        if template_language:
            return template_language
        if self._contains_english_request(f"{message}\n{requirements}"):
            return "en"
        items = sources or []
        for item in items[:8]:
            name = str(item.get("name") or "")
            text = str(item.get("text") or "")[:2400]
            if self._contains_english_request(f"{name}\n{text}"):
                return "en"
        return "zh" if str(writing_type or "").strip().lower() == "grant" else "en"

    def _resolve_model_provider(self, payload: dict[str, Any]) -> str:
        requested = str(payload.get("model_provider") or "").strip()
        return normalize_model_provider(requested or load_default_model_provider())

    def _resolve_model_name(self, config: AppConfig, provider: str, purpose: str = "runner") -> str:
        if provider == "ds":
            return "deepseek-chat"
        return config.planner_model if purpose == "planner" else config.runner_model

    def _decode_uploaded_text(self, item: dict[str, Any]) -> dict[str, Any]:
        name = str(item.get("name") or "upload").strip() or "upload"
        content_type = str(item.get("content_type") or "").strip()
        raw_text = item.get("text")
        if isinstance(raw_text, str) and raw_text.strip():
            return {
                "name": name,
                "content_type": content_type,
                "kind": Path(name).suffix.lower().lstrip("."),
                "text": raw_text[:120000],
            }
        encoded = str(item.get("content_base64") or "")
        if not encoded:
            return {"name": name, "content_type": content_type, "kind": "", "text": ""}
        try:
            raw = b64decode(encoded)
        except binascii.Error:
            raw = encoded.encode("utf-8", errors="ignore")
        suffix = Path(name).suffix.lower()
        text = ""
        kind = suffix.lstrip(".")
        if suffix in {".txt", ".md", ".tex", ".json", ".csv", ".py", ".bib", ".yaml", ".yml"}:
            text = raw.decode("utf-8", errors="ignore")
        elif suffix == ".pdf":
            with tempfile.TemporaryDirectory(prefix="sa-upload-") as temp_dir:
                source = Path(temp_dir) / "upload.pdf"
                target = Path(temp_dir) / "upload.txt"
                source.write_bytes(raw)
                completed = subprocess.run(
                    ["pdftotext", str(source), str(target)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if completed.returncode == 0 and target.exists():
                    text = target.read_text(encoding="utf-8", errors="ignore")
                else:
                    text = ""
        elif suffix == ".docx":
            with tempfile.TemporaryDirectory(prefix="sa-docx-") as temp_dir:
                source = Path(temp_dir) / "upload.docx"
                source.write_bytes(raw)
                with zipfile.ZipFile(source) as archive:
                    xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
                text = re.sub(r"<[^>]+>", " ", xml)
                text = re.sub(r"\s+", " ", text).strip()
        else:
            text = raw.decode("utf-8", errors="ignore")
        text = re.sub(r"\s+\n", "\n", text)
        return {
            "name": name,
            "content_type": content_type,
            "kind": kind,
            "text": text[:120000],
        }

    def _decode_project_upload(self, item: dict[str, Any]) -> dict[str, Any]:
        name = str(item.get("name") or "upload").strip() or "upload"
        rel_path = str(item.get("path") or name).strip() or name
        content_type = str(item.get("content_type") or "").strip()
        raw_text = item.get("text")
        if isinstance(raw_text, str):
            return {
                "name": name,
                "path": rel_path,
                "content_type": content_type,
                "content": raw_text,
            }
        encoded = str(item.get("content_base64") or "")
        if not encoded:
            return {
                "name": name,
                "path": rel_path,
                "content_type": content_type,
                "content": "",
            }
        try:
            raw = b64decode(encoded)
        except binascii.Error:
            raw = encoded.encode("utf-8", errors="ignore")
        suffix = Path(rel_path).suffix.lower()
        if suffix in {".tex", ".cls", ".sty", ".bib", ".bst", ".txt", ".md", ".json", ".csv", ".yaml", ".yml", ".py", ".xml", ".html", ".css", ".js", ".ts"}:
            return {
                "name": name,
                "path": rel_path,
                "content_type": content_type,
                "content": raw.decode("utf-8", errors="ignore"),
            }
        return {
            "name": name,
            "path": rel_path,
            "content_type": content_type,
            "content_bytes": raw,
        }

    def _send_sse_event(self, event: str, payload: dict[str, Any]) -> None:
        body = (
            f"event: {event}\n"
            + "data: "
            + json.dumps(payload, ensure_ascii=False)
            + "\n\n"
        ).encode("utf-8")
        self.wfile.write(body)
        self.wfile.flush()

    def _serve_workflow_stream(self, project_id: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = False
        last_updated = ""
        started = time.time()
        try:
            while True:
                workflow = get_workflow_state(project_id)
                updated_at = str(workflow.get("updated_at") or "")
                if updated_at != last_updated:
                    self._send_sse_event("workflow", {"workflow": workflow})
                    last_updated = updated_at
                else:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                if time.time() - started > 300:
                    break
                time.sleep(2.0)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                self._send_sse_event("error", {"error": str(exc)})
            except Exception:
                return

    def _infer_writing_type(self, message: str, requirements: str = "", template_id: str = "") -> str:
        text = f"{message}\n{requirements}".lower()
        # Thesis/book templates always use academic writing type regardless of
        # requirements text mentioning "基金" / "申报书" — the template structure
        # dictates the format, not the writing style hints.
        if self._is_thesis_template(template_id):
            return "academic"
        if any(token in text for token in ["基金", "申报书", "proposal", "grant", "nsfc"]):
            return "grant"
        return "academic"

    def _is_thesis_template(self, template_id: str) -> bool:
        """Return True if the template is a thesis/book that defines chapter structure."""
        tid = str(template_id or "").strip()
        if not tid:
            return False
        if tid in {"hithesisbook", "hithesisart", "hithesisartplus"}:
            return True
        doc_class_name = self._template_document_class(tid)
        return doc_class_name in {"book", "ctexbook", "report", "ctexrep"}

    def _template_document_class(self, template_id: str) -> str:
        try:
            template = get_template(template_id)
        except Exception:
            return ""
        structure = template.get("structure") or {}
        if isinstance(structure, dict):
            return str(structure.get("document_class") or "").strip()
        return ""

    def _infer_project_title(self, user_title: str, message: str, sources: list[dict[str, Any]]) -> str:
        title = str(user_title or "").strip()
        if title:
            return title[:120]
        for item in sources[:4]:
            name = str(item.get("name") or "").strip()
            stem = Path(name).stem.strip()
            if stem:
                return stem[:120]
        lowered = message.lower()
        if "申报书" in message or "proposal" in lowered:
            return "项目申报书"
        if "毕业论文" in message or "thesis" in lowered:
            return "毕业论文"
        if "综述" in message or "review" in lowered:
            return "文献综述"
        if "论文" in message or "paper" in lowered:
            return "学术论文"
        return message[:120] or "写作项目"

    def _source_brief(self, sources: list[dict[str, Any]]) -> str:
        if not sources:
            return "无上传材料。"
        rows = []
        for index, item in enumerate(sources[:8], start=1):
            rows.append(
                f"[{index}] {item.get('name', '')}\n"
                f"类型: {item.get('kind', '') or item.get('content_type', '')}\n"
                f"内容摘录: {str(item.get('text', ''))[:1200]}"
            )
        return "\n\n".join(rows)

    def _extract_json_block(self, raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        try:
            value = json.loads(cleaned)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.S)
            if not match:
                return {}
            try:
                value = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}

    def _format_recent_context(self, items: list[dict[str, Any]], limit: int = 10) -> str:
        rows = []
        for item in items[-limit:]:
            rows.append(
                f"- [{item.get('role', '')}/{item.get('kind', '')}] "
                f"{item.get('file_path', '') or 'project'}: {item.get('summary', '')}"
            )
        return "\n".join(rows) if rows else "暂无最近上下文记忆。"

    def _format_project_snapshot(self, project_context: dict[str, Any], limit: int = 6) -> str:
        project = project_context.get("project") or {}
        sections = project_context.get("sections") or []
        source_files = project_context.get("source_files") or []
        rows = [
            f"项目：{project.get('title', '')}",
            f"目标：{project.get('goal', '')}",
            f"要求：{project_context.get('requirements', '')}",
        ]
        if sections:
            rows.append("章节：" + " / ".join(str(item.get("title") or "") for item in sections[:limit]))
        if source_files:
            rows.append(
                "源材料："
                + " / ".join(
                    f"{item.get('name', '')}: {str(item.get('excerpt') or '')[:180]}"
                    for item in source_files[:limit]
                )
            )
        return "\n".join(row for row in rows if str(row).strip())

    def _format_section_memories(self, items: list[dict[str, Any]], limit: int = 8) -> str:
        rows = []
        for item in items[-limit:]:
            evidence_keys = ", ".join(str(key) for key in item.get("evidence_keys", [])[:6])
            rows.append(
                f"- {item.get('section', '')} | 路径: {item.get('path', '')} | "
                f"记忆: {item.get('memory', '')} | 证据: {evidence_keys or '无'}"
            )
        return "\n".join(rows) if rows else "暂无章节记忆。"

    def _format_evidence_cards(self, evidence_memory: dict[str, Any], limit: int = 8) -> str:
        cards = evidence_memory.get("cards", []) if isinstance(evidence_memory, dict) else []
        rows = []
        for item in cards[:limit]:
            rows.append(
                f"[{item.get('key', '')}] {item.get('title', '')} | "
                f"{item.get('claim', '')} | {item.get('why_it_matters', '')}"
            )
        return "\n".join(rows) if rows else "暂无证据记忆卡。"

    def _format_workspace_context(self, workspace_index: dict[str, Any], limit: int = 12) -> str:
        if not isinstance(workspace_index, dict) or not workspace_index:
            return "暂无导入代码工作区。"
        rows = [
            f"工作区: {workspace_index.get('workspace_name', '')}",
            f"路径: {workspace_index.get('workspace_path', '')}",
            f"文件数: {workspace_index.get('file_count', 0)} | 结果图数: {workspace_index.get('figure_count', 0)}",
        ]
        entries = workspace_index.get("entries") or []
        for item in entries[:limit]:
            rows.append(
                f"- [{item.get('section', '')}/{item.get('role', '')}] "
                f"{item.get('path', '')}: {item.get('excerpt', '')}"
            )
        figures = workspace_index.get("figures") or []
        if figures:
            rows.append(
                "可用图片: "
                + " / ".join(
                    f"{item.get('path', '')} -> {item.get('latex_path', '')}"
                    for item in figures[: min(len(figures), 6)]
                )
            )
        return "\n".join(rows)

    def _format_workspace_focus(self, workspace_index: dict[str, Any]) -> str:
        if not isinstance(workspace_index, dict) or not workspace_index:
            return "暂无导入代码工作区。"
        entries = workspace_index.get("entries") or []
        focus_names = ["train.py", "model.py", "predict.py", "label.py"]
        focused_entries: list[dict[str, Any]] = []
        for name in focus_names:
            for item in entries:
                if str(item.get("path") or "").endswith(name):
                    focused_entries.append(item)
                    break
        if not focused_entries:
            focused_entries = entries[:4]
        rows = [
            f"工作区: {workspace_index.get('workspace_name', '')}",
            f"路径: {workspace_index.get('workspace_path', '')}",
        ]
        for item in focused_entries[:4]:
            rows.append(
                f"- 关键文件 {item.get('path', '')} | {item.get('section', '')}/{item.get('role', '')}: "
                f"{str(item.get('excerpt') or '')[:320]}"
            )
        figures = workspace_index.get("figures") or []
        if figures:
            rows.append(
                "优先结果图: "
                + " / ".join(
                    f"{item.get('path', '')} -> {item.get('latex_path', '')}"
                    for item in figures[:3]
                )
            )
        return "\n".join(rows)

    def _citation_author_token(self, author: str) -> str:
        cleaned = re.sub(r"[^A-Za-z\u4e00-\u9fff ]+", " ", str(author or "")).strip()
        if not cleaned:
            return "ref"
        if re.search(r"[\u4e00-\u9fff]", cleaned):
            return re.sub(r"\s+", "", cleaned)[:4] or "ref"
        tokens = [token for token in cleaned.split() if token]
        return (tokens[-1] if tokens else cleaned).lower()[:16] or "ref"

    def _library_citation_cards(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        used: set[str] = set()
        cards: list[dict[str, Any]] = []
        for item in items:
            authors = item.get("authors") or []
            year_match = re.search(r"(19|20)\d{2}", str(item.get("year") or ""))
            year = year_match.group(0) if year_match else "nd"
            title_tokens = re.findall(r"[A-Za-z0-9]+", str(item.get("title") or ""))
            base = re.sub(
                r"[^A-Za-z0-9\u4e00-\u9fff]+",
                "",
                f"{self._citation_author_token(str(authors[0] if authors else 'ref'))}{year}{(title_tokens[0].lower() if title_tokens else 'paper')[:12]}",
            ) or f"ref{year}"
            key = base
            suffix = 2
            while key in used:
                key = f"{base}{suffix}"
                suffix += 1
            used.add(key)
            cards.append(
                {
                    "key": key,
                    "title": item.get("title", ""),
                    "year": item.get("year", ""),
                    "venue": item.get("venue", ""),
                    "abstract": str(item.get("abstract", ""))[:260],
                }
            )
        return cards

    def _format_library_evidence(self, items: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        cards = self._library_citation_cards(items)
        rows = []
        for card in cards:
            rows.append(
                f"[{card.get('key', '')}] {card.get('title', '')} | {card.get('year', '')} | {card.get('venue', '')}\n"
                f"摘要: {card.get('abstract', '')}"
            )
        return ("\n\n".join(rows) if rows else "无明显匹配本地文献。"), cards

    def _is_long_form_request(self, message: str, requirements: str = "") -> bool:
        text = f"{message}\n{requirements}".lower()
        tokens = [
            "长文",
            "综述",
            "survey",
            "review",
            "proposal",
            "申报书",
            "毕业论文",
            "thesis",
            "全文",
            "整篇",
            "完整论文",
            "完整文章",
            "完整初稿",
            "自动完成",
        ]
        return any(token in text for token in tokens)

    def _needs_strict_format(self, message: str, requirements: str = "", source_items: list[dict[str, Any]] | None = None) -> bool:
        text = f"{message}\n{requirements}".lower()
        tokens = [
            "严格格式",
            "严格按照",
            "必须按照",
            "格式要求",
            "排版要求",
            "模板要求",
            "规范要求",
            "submission",
            "camera-ready",
            "author guideline",
        ]
        if any(token in text for token in tokens):
            return True
        items = source_items or []
        for item in items[:6]:
            name = str(item.get("name") or "").lower()
            excerpt = str(item.get("text") or "")[:1600].lower()
            if any(token in name or token in excerpt for token in ["格式", "模板", "要求", "guide", "guideline", "specification"]):
                return True
        return False

    def _detect_structure_change_intent(self, message: str, requirements: str = "") -> str:
        """Detect whether the user explicitly requests template structure changes.

        Returns an empty string if structure change IS requested (don't block it),
        or a warning string to inject into the prompt if NOT requested.
        """
        text = f"{message}\n{requirements}"
        structure_change_tokens = [
            "增加一章", "新增一章", "添加一章", "加一章", "补充一章",
            "删除一章", "去掉一章", "移除一章", "删掉一章",
            "增加一节", "新增一节", "添加一节", "加一节",
            "删除一节", "去掉一节", "移除一节",
            "调整结构", "修改框架", "改变结构", "调整框架",
            "重新排序", "调整顺序", "调换顺序", "交换章节",
            "合并章节", "拆分章节", "重组结构",
            "add a chapter", "add a section", "add chapter", "add section",
            "remove chapter", "remove section", "delete chapter", "delete section",
            "reorder", "restructure", "change structure", "modify framework",
            "merge chapters", "split chapter", "reorganize",
            "把...移到", "把章节", "章节调整",
        ]
        if any(token in text.lower() for token in structure_change_tokens):
            return ""
        return (
            "⚠️ **用户未要求修改模板框架**。本轮你只能填充正文内容，"
            "不得新增、删除、重排序或重命名任何章节。章节清单以模板理解中列出的为准。"
        )

    def do_OPTIONS(self) -> None:
        self._send_json({"status": "ok"})

    def do_GET(self) -> None:
        from urllib.parse import urlparse

        request_path = urlparse(self.path).path
        static_files = {
            "/": ("apps/web/index.html", "text/html; charset=utf-8"),
            "/workspace": ("apps/web/workspace.html", "text/html; charset=utf-8"),
            "/writing": ("apps/web/writing.html", "text/html; charset=utf-8"),
            "/writing-section": ("apps/web/writing-section.html", "text/html; charset=utf-8"),
            "/library": ("apps/web/library.html", "text/html; charset=utf-8"),
            "/app.js": ("apps/web/app.js", "application/javascript; charset=utf-8"),
            "/writing.js": ("apps/web/writing.js", "application/javascript; charset=utf-8"),
            "/writing-section.js": ("apps/web/writing-section.js", "application/javascript; charset=utf-8"),
            "/home.js": ("apps/web/home.js", "application/javascript; charset=utf-8"),
            "/library.js": ("apps/web/library.js", "application/javascript; charset=utf-8"),
            "/styles.css": ("apps/web/styles.css", "text/css; charset=utf-8"),
        }
        static_target = static_files.get(request_path)
        if static_target:
            self._send_file(static_target[0], content_type=static_target[1])
            return
        if request_path == "/api/download-queue":
            config = load_config()
            self._send_json({"batches": list_download_batches(config)})
            return
        if request_path == "/api/attention/jobs":
            self._send_json({"jobs": list_attention_jobs()})
            return
        if request_path == "/api/model-settings":
            config = load_config()
            self._send_json(self._model_settings_payload(config))
            return
        if request_path.startswith("/api/library/search"):
            params = self._query_params()
            config = load_config()
            limit = int(params.get("limit", "10") or "10")
            result = {
                "query": params.get("q", ""),
                "stats": library_stats(config),
                "items": search_library(config, params.get("q", ""), limit=limit),
            }
            self._send_json(result)
            return
        if request_path.startswith("/api/library/graph"):
            params = self._query_params()
            config = load_config()
            limit = int(params.get("limit", "18") or "18")
            result = library_graph(config, params.get("q", ""), limit=limit)
            self._send_json(result)
            return
        if request_path.startswith("/api/library/topics"):
            params = self._query_params()
            config = load_config()
            limit = int(params.get("limit", "12") or "12")
            result = {
                "query": params.get("q", ""),
                "items": search_library_topics(config, params.get("q", ""), limit=limit),
            }
            self._send_json(result)
            return
        if request_path.startswith("/api/library/topic"):
            params = self._query_params()
            config = load_config()
            topic_ref = params.get("id", "") or params.get("slug", "")
            if not topic_ref:
                self._send_json(
                    {"error": "topic id or slug is required"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            limit = int(params.get("limit", "30") or "30")
            result = get_topic_library(config, topic_ref, limit=limit)
            if not result:
                self._send_json({"error": "topic not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(result)
            return
        if request_path.startswith("/api/library/paper"):
            params = self._query_params()
            config = load_config()
            paper_id = params.get("id", "")
            if not paper_id:
                self._send_json(
                    {"error": "paper id is required"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            result = get_library_paper_detail(config, paper_id)
            if not result:
                self._send_json({"error": "paper not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"paper": result})
            return
        if request_path.startswith("/api/library/reports"):
            params = self._query_params()
            config = load_config()
            limit = int(params.get("limit", "20") or "20")
            self._send_json({"items": list_survey_reports(config, limit=limit)})
            return
        if request_path.startswith("/api/library/report"):
            params = self._query_params()
            config = load_config()
            report_id = params.get("id", "")
            if not report_id:
                self._send_json({"error": "report id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            report = read_survey_report(config, report_id)
            if not report:
                self._send_json({"error": "report not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"report": report})
            return
        if request_path.startswith("/api/templates"):
            params = self._query_params()
            template_id = params.get("id", "")
            include_source = params.get("include_source", "") in {"1", "true", "yes"}
            if template_id:
                self._send_json({"template": get_template(template_id, include_source=include_source)})
                return
            self._send_json(list_templates(params.get("category", "")))
            return
        if request_path.startswith("/api/writing/project/pdf"):
            project_id = self._query_params().get("id", "")
            try:
                body = read_pdf_bytes(project_id)
            except FileNotFoundError:
                self._send_json({"error": "pdf not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", "inline; filename=\"manuscript.pdf\"")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path == "/api/writing/projects":
            self._send_json(list_projects())
            return
        if request_path.startswith("/api/writing/project/context"):
            params = self._query_params()
            project_id = params.get("project_id", "") or params.get("id", "")
            rel_path = params.get("path", "")
            if not project_id:
                self._send_json({"error": "project_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json({"context": load_project_context(project_id, rel_path)})
            except FileNotFoundError:
                self._send_json({"error": "writing project not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if request_path.startswith("/api/writing/workflow/context"):
            params = self._query_params()
            project_id = params.get("project_id", "") or params.get("id", "")
            section_id = params.get("section_id", "")
            if not project_id or not section_id:
                self._send_json({"error": "project_id and section_id are required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json({"context": compress_context(project_id, section_id)})
            except FileNotFoundError:
                self._send_json({"error": "writing project not found"}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if request_path.startswith("/api/writing/workflow/stream"):
            params = self._query_params()
            project_id = params.get("project_id", "") or params.get("id", "")
            if not project_id:
                self._send_json({"error": "project_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._serve_workflow_stream(project_id)
            except FileNotFoundError:
                self._send_json({"error": "writing project not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if request_path.startswith("/api/writing/workflow"):
            params = self._query_params()
            project_id = params.get("project_id", "") or params.get("id", "")
            if not project_id:
                self._send_json({"error": "project_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json({"workflow": get_workflow_state(project_id)})
            except FileNotFoundError:
                self._send_json({"error": "writing project not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if request_path.startswith("/api/writing/project/guardrails"):
            params = self._query_params()
            project_id = params.get("project_id", "") or params.get("id", "")
            if not project_id:
                self._send_json({"error": "project_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json(
                    {
                        "guardrails": load_guardrails(project_id),
                        "yaml_text": read_project_guardrails_yaml(project_id),
                    }
                )
            except FileNotFoundError:
                self._send_json({"error": "writing project not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if request_path.startswith("/api/writing/project/workspace"):
            params = self._query_params()
            project_id = params.get("project_id", "") or params.get("id", "")
            if not project_id:
                self._send_json({"error": "project_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json({"workspace": load_workspace_index(project_id)})
            except FileNotFoundError:
                self._send_json({"error": "writing project not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if request_path.startswith("/api/writing/project/file"):
            params = self._query_params()
            project_id = params.get("project_id", "")
            rel_path = params.get("path", "")
            if not project_id or not rel_path:
                self._send_json({"error": "project_id and path are required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json({"file": read_project_file(project_id, rel_path)})
            except FileNotFoundError:
                self._send_json({"error": "project file not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if request_path.startswith("/api/writing/project"):
            project_id = self._query_params().get("id", "")
            if not project_id:
                self._send_json({"error": "project_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json({"project": load_project(project_id)})
            except FileNotFoundError:
                self._send_json({"error": "writing project not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if request_path.startswith("/api/attention/job"):
            job_id = self._query_params().get("id", "")
            job = get_attention_job(job_id)
            if not job:
                self._send_json({"error": "attention job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"job": job})
            return
        if request_path != "/health":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        config = load_config()
        self._send_json(
            {
                "status": "ok",
                "config_path": str(config.path),
                "library_root": str(config.root_dir),
                "stats": library_stats(config),
            }
        )

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            config = load_config()
            if self.path == "/api/capture":
                result = persist_capture(config, payload)
                self._send_json(result, status=HTTPStatus.CREATED)
                return
            if self.path == "/api/chat":
                result = self._handle_chat(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/config/apply":
                result = self._handle_apply_patch(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/model-settings":
                result = self._handle_model_settings(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/search":
                result = self._handle_search(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/agent/run":
                result = self._handle_agent_run(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/attention/run":
                payload = {**payload, "model_provider": self._resolve_model_provider(payload)}
                result = start_attention_job(payload, config_path=str(config.path))
                self._send_json(result, status=HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/library/report/generate":
                result = generate_library_survey_report(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/research-workflow/run":
                payload = {**payload, "model_provider": self._resolve_model_provider(payload)}
                result = run_research_workflow(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/templates/download":
                template_id = str(payload.get("template_id") or "").strip()
                if not template_id:
                    raise ValueError("template_id is required")
                result = download_template(template_id)
                self._send_json({"template": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/templates/resolve":
                request_text = str(payload.get("request") or "").strip()
                if not request_text:
                    raise ValueError("request is required")
                result = resolve_template_request(
                    request_text,
                    api_key=str(payload.get("api_key") or "").strip(),
                    model=str(payload.get("model") or "kimi-k2.5"),
                )
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/create":
                archive = payload.get("archive")
                if isinstance(archive, dict) and archive.get("content_base64"):
                    result = create_project(payload)
                    encoded = str(archive.get("content_base64") or "")
                    try:
                        archive_bytes = b64decode(encoded)
                    except binascii.Error as exc:
                        raise ValueError(f"invalid archive payload: {exc}") from exc
                    result = import_project_archive(
                        str(result.get("project_id") or ""),
                        str(archive.get("name") or "project.zip"),
                        archive_bytes,
                        replace_project=True,
                    )
                    self._send_json({"project": result}, status=HTTPStatus.CREATED)
                    return
                uploaded = payload.get("files")
                if isinstance(uploaded, list) and uploaded:
                    decoded = [self._decode_project_upload(item) for item in uploaded if isinstance(item, dict)]
                    result = create_project({**payload, "files": decoded, "replace_project": True})
                    record_project_turn(
                        str(result.get("project_id") or ""),
                        "assistant",
                        "已根据手动上传的模板或项目源码创建新项目。",
                        kind="import",
                        metadata={"file_count": len(decoded), "created_from_upload": True},
                    )
                    self._send_json({"project": result}, status=HTTPStatus.CREATED)
                    return
                result = create_project(payload)
                self._send_json({"project": result}, status=HTTPStatus.CREATED)
                return
            if self.path == "/api/writing/project/delete":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = delete_project(project_id)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/import":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                archive = payload.get("archive")
                replace_project = bool(payload.get("replace_project", True))
                if isinstance(archive, dict) and archive.get("content_base64"):
                    encoded = str(archive.get("content_base64") or "")
                    try:
                        archive_bytes = b64decode(encoded)
                    except binascii.Error as exc:
                        raise ValueError(f"invalid archive payload: {exc}") from exc
                    result = import_project_archive(
                        project_id,
                        str(archive.get("name") or "project.zip"),
                        archive_bytes,
                        replace_project=replace_project,
                    )
                    self._send_json({"project": result}, status=HTTPStatus.OK)
                    return
                uploaded = payload.get("files") or []
                if not isinstance(uploaded, list) or not uploaded:
                    raise ValueError("archive or files is required")
                decoded = [self._decode_project_upload(item) for item in uploaded if isinstance(item, dict)]
                current = load_project(project_id)
                result = create_project(
                    {
                        "project_id": project_id,
                        "title": current.get("title") or "Untitled Project",
                        "author": current.get("author") or "Scientific Agent",
                        "goal": current.get("goal") or "",
                        "query": current.get("query") or "",
                        "requirements": current.get("requirements") or "",
                        "writing_type": current.get("writing_type") or "academic",
                        "writing_language": current.get("writing_language") or "en",
                        "main_tex": current.get("main_tex") or "main.tex",
                        "files": decoded,
                        "replace_project": replace_project,
                    }
                )
                record_project_turn(
                    project_id,
                    "assistant",
                    "已导入手动上传的项目源码。",
                    kind="import",
                    metadata={"file_count": len(decoded), "replace_project": replace_project},
                )
                self._send_json({"project": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/sources":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                uploaded = payload.get("files") or []
                if not isinstance(uploaded, list):
                    raise ValueError("files must be a list")
                decoded = [self._decode_uploaded_text(item) for item in uploaded if isinstance(item, dict)]
                sources = save_project_sources(project_id, decoded)
                project = load_project(project_id)
                self._send_json({"project": project, "sources": sources}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/workspace/import":
                project_id = str(payload.get("project_id") or "").strip()
                workspace_path = str(payload.get("workspace_path") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = import_local_workspace(project_id, workspace_path)
                self._send_json({"project": load_project(project_id), "workspace": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/workspace/figure":
                project_id = str(payload.get("project_id") or "").strip()
                target_path = str(payload.get("target_path") or "").strip()
                figure_rel_path = str(payload.get("figure_rel_path") or "").strip()
                result = insert_workspace_figure(
                    project_id,
                    target_path,
                    figure_rel_path,
                    caption=str(payload.get("caption") or ""),
                    label=str(payload.get("label") or ""),
                    width=str(payload.get("width") or "0.92\\linewidth"),
                )
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/analyze-chapters":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                manifest = analyze_chapters_with_llm(project_id)
                self._send_json({"manifest": manifest}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/guardrails/analyze":
                project_id = str(payload.get("project_id") or "").strip()
                api_key = str(payload.get("api_key") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = analyze_project_template_guardrails(project_id, api_key=api_key)
                self._send_json(
                    {
                        "guardrails": result,
                        "yaml_text": read_project_guardrails_yaml(project_id),
                    },
                    status=HTTPStatus.OK,
                )
                return
            if self.path == "/api/writing/project/guardrails/save":
                project_id = str(payload.get("project_id") or "").strip()
                yaml_text = str(payload.get("yaml_text") or "")
                if not project_id:
                    raise ValueError("project_id is required")
                result = save_project_guardrails_yaml(project_id, yaml_text)
                self._send_json(
                    {
                        "guardrails": result,
                        "yaml_text": read_project_guardrails_yaml(project_id),
                    },
                    status=HTTPStatus.OK,
                )
                return
            if self.path == "/api/writing/project/file/save":
                result = save_project_file(payload)
                project_id = str(payload.get("project_id") or "").strip()
                rel_path = str(payload.get("path") or "").strip()
                content = str(payload.get("content") or "")
                if project_id and rel_path:
                    update_section_memory(project_id, rel_path, content, prompt="manual save")
                self._send_json({"file": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/file/create":
                result = create_project_file(payload)
                self._send_json({"file": result}, status=HTTPStatus.CREATED)
                return
            if self.path == "/api/writing/project/meta":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                current = load_project(project_id)
                result = create_project(
                    {
                        "project_id": project_id,
                        "title": str(payload.get("title") or current.get("title") or "Untitled Project"),
                        "author": str(payload.get("author") or current.get("author") or "Scientific Agent"),
                        "goal": str(payload.get("goal") or current.get("goal") or ""),
                        "query": str(payload.get("query") or current.get("query") or ""),
                        "requirements": str(payload.get("requirements") or current.get("requirements") or ""),
                        "writing_type": str(payload.get("writing_type") or current.get("writing_type") or "academic"),
                        "writing_language": self._infer_writing_language(
                            message=str(payload.get("goal") or current.get("goal") or ""),
                            requirements=str(payload.get("requirements") or current.get("requirements") or ""),
                            writing_type=str(payload.get("writing_type") or current.get("writing_type") or "academic"),
                            template_id=str(current.get("template_id") or ""),
                            project=current,
                            explicit=str(payload.get("writing_language") or current.get("writing_language") or ""),
                        ),
                        "main_tex": str(payload.get("main_tex") or current.get("main_tex") or "main.tex"),
                    }
                )
                self._send_json({"project": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/compile":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = compile_project(project_id)
                self._send_json({"compile": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/exploration":
                project_id = str(payload.get("project_id") or "").strip()
                topic = str(payload.get("topic") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = get_exploration_report(project_id, topic)
                self._send_json({"exploration": result, "workflow": get_workflow_state(project_id)}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/exploration/select":
                project_id = str(payload.get("project_id") or "").strip()
                selected_topic = str(payload.get("selected_topic") or payload.get("topic") or "").strip()
                selection_id = str(payload.get("selection_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                if not selected_topic:
                    raise ValueError("selected_topic is required")
                result = select_exploration_topic(project_id, selected_topic, selection_id=selection_id)
                self._send_json({"workflow": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/outline/start":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = start_outline_negotiation(project_id)
                self._send_json({"workflow": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/outline/confirm":
                project_id = str(payload.get("project_id") or "").strip()
                section_id = str(payload.get("section_id") or "").strip()
                choice = str(payload.get("choice") or payload.get("strategy_id") or "").strip()
                strategy_label = str(payload.get("strategy_label") or "").strip()
                custom_note = str(payload.get("custom_note") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                if not section_id:
                    raise ValueError("section_id is required")
                if not choice and not custom_note:
                    raise ValueError("choice or custom_note is required")
                result = negotiate_section(
                    project_id,
                    section_id,
                    choice or "custom",
                    strategy_label=strategy_label,
                    custom_note=custom_note,
                )
                self._send_json({"workflow": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/order/recommend":
                project_id = str(payload.get("project_id") or "").strip()
                topic_type = str(payload.get("topic_type") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = recommend_writing_order(project_id, topic_type or None)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/order":
                project_id = str(payload.get("project_id") or "").strip()
                ordered_section_ids = payload.get("ordered_section_ids") or payload.get("section_ids") or []
                if not project_id:
                    raise ValueError("project_id is required")
                if not isinstance(ordered_section_ids, list):
                    raise ValueError("ordered_section_ids must be a list")
                result = set_writing_order(project_id, ordered_section_ids)
                self._send_json({"workflow": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/section/start":
                project_id = str(payload.get("project_id") or "").strip()
                section_id = str(payload.get("section_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                if not section_id:
                    raise ValueError("section_id is required")
                result = start_chapter_writing(project_id, section_id)
                self._send_json({"workflow": result}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/section/save":
                project_id = str(payload.get("project_id") or "").strip()
                section_id = str(payload.get("section_id") or "").strip()
                content = str(payload.get("content") or "")
                prompt = str(payload.get("prompt") or "workflow draft save")
                if not project_id:
                    raise ValueError("project_id is required")
                if not section_id:
                    raise ValueError("section_id is required")
                result = save_section_draft(project_id, section_id, content, prompt=prompt)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/citations/apply":
                project_id = str(payload.get("project_id") or "").strip()
                section_id = str(payload.get("section_id") or "").strip()
                decisions = payload.get("citation_decisions") or payload.get("decisions") or {}
                if not project_id:
                    raise ValueError("project_id is required")
                if not section_id:
                    raise ValueError("section_id is required")
                if not isinstance(decisions, dict):
                    raise ValueError("citation_decisions must be an object")
                normalized = {
                    str(key).strip(): [str(item).strip() for item in value if str(item).strip()]
                    for key, value in decisions.items()
                    if str(key).strip() and isinstance(value, list)
                }
                result = apply_section_citations(project_id, section_id, normalized)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/section/lock":
                project_id = str(payload.get("project_id") or "").strip()
                section_id = str(payload.get("section_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                if not section_id:
                    raise ValueError("section_id is required")
                result = lock_chapter(project_id, section_id)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/section/unlock":
                project_id = str(payload.get("project_id") or "").strip()
                section_id = str(payload.get("section_id") or "").strip()
                cascade = bool(payload.get("cascade", False))
                if not project_id:
                    raise ValueError("project_id is required")
                if not section_id:
                    raise ValueError("section_id is required")
                result = unlock_chapter(project_id, section_id, cascade=cascade)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/workflow/final-review":
                project_id = str(payload.get("project_id") or "").strip()
                if not project_id:
                    raise ValueError("project_id is required")
                result = run_final_review(project_id)
                self._send_json({"review": result, "workflow": get_workflow_state(project_id)}, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/assist":
                result = self._handle_writing_assist(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/section/generate":
                result = self._handle_section_generation(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/chat":
                result = self._handle_writing_chat(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/audit":
                result = self._handle_writing_audit(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            if self.path == "/api/writing/project/audit/fix":
                result = self._handle_writing_audit_fix(config, payload)
                self._send_json(result, status=HTTPStatus.OK)
                return
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        except json.JSONDecodeError as exc:
            self._send_json(
                {"error": "invalid json", "detail": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        except Exception as exc:  # pragma: no cover - smoke path only
            self._send_json(
                {"error": "capture failed", "detail": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _query_params(self) -> dict[str, str]:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        return {
            key: values[0] if values else ""
            for key, values in parse_qs(parsed.query).items()
        }

    def _handle_chat(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        user_message = str(payload.get("message", "")).strip()
        if not user_message:
            raise ValueError("Message is required.")
        api_key = str(payload.get("api_key", "")).strip() or None
        model_provider = self._resolve_model_provider(payload)
        result = chat_with_kimi(
            config,
            user_message,
            api_key=api_key,
            model=self._resolve_model_name(config, model_provider, "planner"),
            provider=model_provider,
        )
        preview = preview_config_update(config, result.patch)
        return {
            "reply": result.content,
            "suggested_patch": result.patch,
            "agent_plan": result.plan,
            "config_preview": preview,
            "model": self._resolve_model_name(config, model_provider, "planner"),
            "model_provider": model_provider,
        }

    def _model_settings_payload(self, config: AppConfig, provider: str | None = None) -> dict[str, Any]:
        resolved = normalize_model_provider(provider or load_default_model_provider())
        kimi_key = load_provider_api_key("kimi")
        ds_key = load_provider_api_key("ds")
        active_key = ds_key if resolved == "ds" else kimi_key
        return {
            "provider": resolved,
            "label": provider_label(resolved),
            "model": self._resolve_model_name(config, resolved, "runner"),
            "api_base": provider_api_base(resolved),
            "has_key": bool(active_key),
            "key_masked": mask_api_key(active_key),
            "has_kimi_key": bool(kimi_key),
            "kimi_key_masked": mask_api_key(kimi_key),
            "kimi_api_base": provider_api_base("kimi"),
            "has_ds_key": bool(ds_key),
            "ds_key_masked": mask_api_key(ds_key),
            "ds_api_base": provider_api_base("ds"),
        }

    def _handle_model_settings(self, config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        provider = save_default_model_provider(str(payload.get("model_provider") or "kimi"))
        api_key = str(payload.get("api_key") or "").strip()
        if api_key:
            save_provider_api_key(provider, api_key)
        if "api_base" in payload:
            save_provider_api_base(provider, str(payload.get("api_base") or ""))
        model = str(payload.get("model") or "").strip()
        if model:
            apply_config_update(
                config,
                {"models": {
                    "planner": {"provider": provider, "model": model},
                    "runner": {"provider": provider, "model": model},
                }},
            )
        updated_config = load_config()
        return {"status": "ok", **self._model_settings_payload(updated_config, provider)}

    def _handle_apply_patch(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        patch = payload.get("patch", {})
        if not isinstance(patch, dict) or not patch:
            raise ValueError("Non-empty patch is required.")
        written_path = apply_config_update(config, patch)
        updated = load_config(str(written_path))
        return {
            "status": "ok",
            "config_path": str(written_path),
            "config": updated.raw,
        }

    def _handle_search(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ValueError("Query is required.")
        max_results = int(payload.get("max_results", 20))
        auto_download = bool(payload.get("auto_download", False))
        min_score = payload.get("min_score")
        resolved_min_score = None if min_score in (None, "") else float(min_score)
        return run_search_pipeline(
            config,
            query=query,
            max_results=max_results,
            auto_download=auto_download,
            min_score=resolved_min_score,
        )

    def _handle_agent_run(self, config: Any, payload: dict[str, Any]) -> dict[str, Any]:
        patch = payload.get("patch", {})
        plan = payload.get("plan", {}) or {}
        apply_patch_first = bool(payload.get("apply_patch_first", True))

        active_config = config
        applied = False
        if apply_patch_first and isinstance(patch, dict) and patch:
            written_path = apply_config_update(config, patch)
            active_config = load_config(str(written_path))
            applied = True

        query = str(plan.get("query", "")).strip()
        if not query:
            raise ValueError("Agent plan query is required.")

        max_results = int(plan.get("max_results", 20))
        min_score = plan.get("min_score")
        resolved_min_score = None if min_score in (None, "") else float(min_score)
        auto_download = bool(plan.get("auto_download", False))
        search_result = run_search_pipeline(
            active_config,
            query=query,
            max_results=max_results,
            auto_download=auto_download,
            min_score=resolved_min_score,
        )
        return {
            "status": "ok",
            "applied_patch": applied,
            "query": query,
            "search": search_result,
            "config": active_config.raw,
        }

    def _handle_writing_assist(self, config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")

        mode = str(payload.get("mode") or "rewrite").strip() or "rewrite"
        project_id = str(payload.get("project_id") or "").strip()
        file_path = str(payload.get("file_path") or "").strip()
        writing_type = str(payload.get("writing_type") or "academic").strip() or "academic"
        context = str(payload.get("context") or "")
        api_key = str(payload.get("api_key") or "").strip()
        model_provider = self._resolve_model_provider(payload)

        project = {}
        project_context = {}
        if project_id:
            try:
                project = load_project(project_id)
            except FileNotFoundError:
                project = {}
            try:
                project_context = load_project_context(
                    project_id,
                    file_path,
                    include_source_text=False,
                    recent_context_limit=8,
                    section_memory_limit=6,
                    evidence_card_limit=6,
                    conversation_limit=12,
                )
            except FileNotFoundError:
                project_context = {}

        library_items = search_library(
            config, prompt, limit=6,
            api_key=api_key, model_provider=model_provider,
        )
        evidence_lines = []
        for index, item in enumerate(library_items[:6], start=1):
            evidence_lines.append(
                f"[{index}] {item.get('title', '')} | {item.get('year', '')} | {item.get('venue', '')}\n"
                f"摘要: {item.get('abstract', '')[:280]}\n"
                f"总结: {((item.get('summary') or {}).get('summary') or '')[:280]}"
            )
        evidence_text = "\n\n".join(evidence_lines) if evidence_lines else "本地文献库没有检索到明显相关条目。"
        requirement_text = str(payload.get("requirements") or project.get("requirements") or project_context.get("requirements") or "")
        section_context = project_context.get("section") or {}
        section_memories = project_context.get("section_memories") or []
        evidence_memory = project_context.get("evidence_memory") or {}
        recent_context = project_context.get("recent_context") or []
        section_memory_lines = []
        for item in section_memories[-6:]:
            evidence_keys = ", ".join(str(key) for key in item.get("evidence_keys", [])[:6])
            section_memory_lines.append(
                f"- {item.get('section', '')}: {str(item.get('memory', ''))[:220]} | 已用证据: {evidence_keys}"
            )
        section_memory_text = "\n".join(section_memory_lines) if section_memory_lines else "暂无章节历史记忆。"
        evidence_cards = evidence_memory.get("cards", []) if isinstance(evidence_memory, dict) else []
        evidence_memory_lines = []
        for card in evidence_cards[:8]:
            evidence_memory_lines.append(
                f"[{card.get('key', '')}] {card.get('title', '')} | 核心论据: {str(card.get('claim', ''))[:180]}"
            )
        evidence_memory_text = "\n".join(evidence_memory_lines) if evidence_memory_lines else "暂无证据记忆卡片。"
        bibliography_instruction = self._bibliography_instruction(project)
        writing_language = self._infer_writing_language(
            message=prompt,
            requirements=requirement_text,
            writing_type=writing_type,
            template_id=str(project.get("template_id") or ""),
            sources=source_files,
            project=project,
            explicit=str(payload.get("writing_language") or project.get("writing_language") or ""),
        )

        system_prompt = (
            self._template_guardian_prompt()
            + "\n\n"
            + (
                "输出格式：第一段是 1-3 句简短建议；"
                "最后单独一行输出 INSERT_TEXT: 后接可直接插入编辑器的 LaTeX 片段。"
            )
        )
        user_prompt = f"""
助写模式：{mode}
写作类型：{writing_type}
目标语言：{self._language_name(writing_language)}
当前项目：{project.get("title", "")}
当前文件：{file_path or "未指定"}
用户任务：{prompt}

项目目标：
{project.get("goal", "")}

项目附加要求：
{requirement_text or "无"}

当前章节上下文：
标题：{section_context.get("title", "")}
路径：{section_context.get("path", file_path or "")}
索引：{section_context.get("index", "")}

当前文件内容（截断）：
{context[:8000] if context else "当前无文件内容"}

章节历史记忆：
{section_memory_text}

文献记忆卡片：
{evidence_memory_text}

最近上下文记忆：
{self._format_recent_context(recent_context)}

本地文献库证据：
{evidence_text}

参考文献系统：
{bibliography_instruction}

要求：
- 如果 mode 是 rewrite，优先改写当前内容，使其更学术、更连贯。
- 如果 mode 是 continue，续写与当前上下文自然衔接的内容。
- 如果 mode 是 section，直接生成一个完整章节片段。
- 如果 mode 是 outline，生成适合当前项目的章节提纲。
- 如果当前文件属于某一章节，优先延续该章节已建立的论证方向，不要和历史记忆冲突。
- 必须服从"项目附加要求"。
- 尽量利用给定本地证据，但不要虚构具体实验数字。
- Narrative text, headings, captions, and inserted prose must be written in {self._language_name(writing_language)}.
- 保持 LaTeX 语法自然，必要时可输出 \\section{{}}、\\subsection{{}}、itemize 等结构。
""".strip()

        raw = self._chat_completion(
            api_key=api_key,
            model=self._resolve_model_name(config, model_provider, "runner"),
            provider=model_provider,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        reply, insert_text = self._extract_insert_text(raw)
        if project_id:
            record_project_turn(project_id, "user", prompt, kind=f"assist:{mode}", file_path=file_path)
            record_project_turn(
                project_id,
                "assistant",
                reply or insert_text,
                kind=f"assist:{mode}:reply",
                file_path=file_path,
                metadata={"insert_text_excerpt": insert_text[:320]},
            )
        return {
            "status": "ok",
            "mode": mode,
            "project_id": project_id,
            "file_path": file_path,
            "reply": reply,
            "insert_text": insert_text,
            "evidence_count": len(library_items),
            "requirements": requirement_text,
            "section_context": section_context,
            "evidence": [
                {
                    "title": item.get("title", ""),
                    "year": item.get("year", ""),
                    "venue": item.get("venue", ""),
                    "paper_id": item.get("paper_id", ""),
                }
                for item in library_items[:6]
            ],
            "raw": raw,
            "model_provider": model_provider,
        }

    def _handle_writing_chat(self, config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")

        api_key = str(payload.get("api_key") or "").strip()
        model_provider = self._resolve_model_provider(payload)
        project_id = str(payload.get("project_id") or "").strip()
        user_title = str(payload.get("title") or "").strip()
        requirements = str(payload.get("requirements") or "").strip()
        task_strategy = str(payload.get("task_strategy") or "auto").strip() or "auto"
        template_id = str(payload.get("template_id") or "").strip()
        author = str(payload.get("author") or "Scientific Agent").strip() or "Scientific Agent"
        uploaded = payload.get("files") or []
        source_items = [self._decode_uploaded_text(item) for item in uploaded if isinstance(item, dict)] if isinstance(uploaded, list) else []

        if project_id:
            try:
                project = load_project(project_id)
            except FileNotFoundError:
                project_id = ""
                project = {}
        else:
            project = {}

        if not project_id:
            writing_type = self._infer_writing_type(message, requirements, template_id=template_id)
            writing_language = self._infer_writing_language(
                message=message,
                requirements=requirements,
                writing_type=writing_type,
                sources=source_items,
                explicit=str(payload.get("writing_language") or ""),
            )
            project = create_project(
                {
                    "template_id": template_id,
                    "title": self._infer_project_title(user_title, message, source_items),
                    "author": author,
                    "goal": message,
                    "requirements": requirements or message,
                    "writing_type": writing_type,
                    "writing_language": writing_language,
                }
            )
            project_id = project.get("project_id", "")

        record_project_turn(project_id, "user", message, kind="chat", metadata={"requirements": requirements})

        if source_items:
            existing_sources = load_project_sources(project_id, include_text=True)
            save_project_sources(project_id, existing_sources + source_items)

        project = load_project(project_id)
        current_goal = message or project.get("goal", "")
        current_requirements = requirements or project.get("requirements", "") or message
        project_context = load_project_context(
            project_id,
            "",
            include_source_text=False,
            recent_context_limit=8,
            section_memory_limit=6,
            evidence_card_limit=6,
            conversation_limit=12,
        )
        source_files = project_context.get("source_files") or []
        writing_language = self._infer_writing_language(
            message=current_goal,
            requirements=current_requirements,
            writing_type=str(project.get("writing_type") or self._infer_writing_type(message, current_requirements, template_id=str(project.get("template_id") or ""))),
            template_id=str(project.get("template_id") or ""),
            sources=source_files,
            project=project,
            explicit=str(payload.get("writing_language") or project.get("writing_language") or ""),
        )
        structure_digest = project_structure_digest(project_id)
        project_snapshot = self._format_project_snapshot(project_context, limit=6)
        workspace_context_text = self._format_workspace_context(project_context.get("workspace_index") or {}, limit=12)
        bibliography_instruction = self._bibliography_instruction(project)
        workflow_requirements = "\n\n".join(
            part for part in [current_requirements.strip(), bibliography_instruction.strip()] if part
        ).strip()

        if task_strategy == "auto":
            wants_long_form = self._is_long_form_request(message, current_requirements)
            wants_strict = self._needs_strict_format(message, current_requirements, source_files)
        else:
            wants_long_form = task_strategy == "rag-draft"
            wants_strict = task_strategy == "strict-format"
        if task_strategy == "auto" and str(project.get("project_id") or "").strip():
            wants_long_form = True

        if wants_long_form or wants_strict:
            workflow_sources = load_project_sources(project_id, include_text=True)
            workflow_query = " ".join(
                filter(
                    None,
                    [
                        message if wants_long_form else "",
                        current_goal if wants_long_form else "",
                        current_requirements if wants_long_form else "",
                        " ".join(str(item.get("name") or "") for item in source_files[:8]),
                        " ".join(str(item.get("path") or "") for item in (project_context.get("workspace_index") or {}).get("entries", [])[:8]),
                    ],
                )
            )[:320]
            # Build template profile BEFORE workflow so runner/planner get template comprehension
            workflow_template_profile: dict[str, Any] = {}
            try:
                workflow_template_profile = project.get("template_profile") or build_template_profile(
                    project_id, template_id=str(project.get("template_id") or ""),
                    project_dir=Path(project.get("paths", {}).get("dir") or "")
                ) if project_id else {}
            except Exception:
                pass
            workflow_payload = {
                "goal": current_goal,
                "writing_type": project.get("writing_type") or self._infer_writing_type(message, current_requirements, template_id=str(project.get("template_id") or "")),
                "writing_language": writing_language,
                "template_id": str(project.get("template_id") or ""),
                "requirements": workflow_requirements,
                "query": workflow_query,
                "use_literature_pipeline": False,
                "max_literature_results": 12,
                "summarize_limit": 4,
                "rag_limit": 12,
                "api_key": api_key,
                "model_provider": model_provider,
                "planner_model": self._resolve_model_name(config, model_provider, "planner"),
                "runner_model": self._resolve_model_name(config, model_provider, "runner"),
                "run_id": project_id,
                "force_sectional": True,
                "workspace_index": project_context.get("workspace_index") or {},
                "source_materials": workflow_sources,
                "bibliography_profile": project.get("bibliography_profile") or {},
                "template_profile": workflow_template_profile,
                "project_mode": str(project.get("project_mode") or ""),
            }
            workflow_result = run_research_workflow(config, workflow_payload)
            sync_workflow_project(
                project_id,
                workflow_result,
                title=user_title or project.get("title") or self._infer_project_title(user_title, message, source_files),
                goal=current_goal,
                requirements=current_requirements,
                author=author,
                query=workflow_query,
            )

            # Run audit → revise loop with template_profile built above
            audit_result = self._audit_and_revise_loop(
                project_id=project_id,
                api_key=api_key,
                model=self._resolve_model_name(config, model_provider, "runner"),
                provider=model_provider,
                template_profile=workflow_template_profile,
                current_requirements=current_requirements,
                source_query=workflow_query,
                author=project.get("author") or author,
                max_iterations=3,
            )
            compile_result = audit_result.get("final_compile") or compile_project(project_id)
            audit_summaries = audit_result.get("audit_summaries") or []
            final_verdict = audit_summaries[-1].get("verdict", "REVISE") if audit_summaries else "UNKNOWN"

            record_project_turn(
                project_id,
                "assistant",
                f"已完成正式写作并通过审计（{final_verdict}），编译状态：{compile_result.get('status', 'unknown')}",
                kind="workflow:completed",
                metadata={
                    "run_id": workflow_result.get("run_id", ""),
                    "strict_format": wants_strict,
                    "rag_draft": wants_long_form,
                    "section_count": len((workflow_result.get("workspace_sections") or [])),
                    "template_id": str(project.get("template_id") or ""),
                    "compile_status": compile_result.get("status", ""),
                    "audit_iterations": len(audit_summaries),
                    "audit_verdict": final_verdict,
                },
            )
            return {
                "status": workflow_result.get("status", "ok"),
                "mode": "workflow",
                "reply": f"已基于代码工作区、真实文献库和当前模板完成整篇写作并通过审计（{final_verdict}，{len(audit_summaries)} 轮修复）。",
                "project": load_project(project_id),
                "compile": compile_result,
                "audit": audit_summaries,
                "sources": load_project_sources(project_id, include_text=False),
                "workspace_sections": workflow_result.get("workspace_sections", []),
                "workspace_manifest": workflow_result.get("workspace_manifest", {}),
                "evidence": workflow_result.get("evidence", []),
                "messages": workflow_result.get("messages", []),
                "model_provider": model_provider,
            }

        source_brief = self._source_brief(source_files[:6])
        recent_context_text = self._format_recent_context(project_context.get("recent_context") or [], limit=6)
        source_query = " ".join(
            filter(
                None,
                [
                    message,
                    project.get("goal", ""),
                    current_requirements,
                    " ".join(str(item.get("name") or "") for item in source_files[:6]),
                ],
            )
        )[:320]
        library_items = search_library(
            config, source_query, limit=8,
            api_key=api_key, model_provider=model_provider,
        )
        evidence_text, citation_cards = self._format_library_evidence(library_items[:8])
        bibliography_instruction = self._bibliography_instruction(project)
        try:
            template_profile = project.get("template_profile") or build_template_profile(
                project_id, template_id=str(project.get("template_id") or ""),
                project_dir=Path(project.get("paths", {}).get("dir") or "")
            ) if project_id else {}
        except Exception:
            template_profile = {}
        template_comprehension = template_comprehension_prompt(template_profile) if template_profile else ""
        structure_change_warning = self._detect_structure_change_intent(message, current_requirements)

        has_sections = bool(
            (template_profile.get("section_hierarchy", {}).get("titles") or {})
            or (template_profile.get("section_hierarchy", {}).get("frontmatter_titles") or [])
        )
        structure_rule = (
            "模板中**未检测到预定义章节**（可能是空白模板）。"
            "你必须根据模板的文档类和惯例，**主动确定合理章节结构**（如 Abstract, Introduction, Related Work, Method, Experiments, Conclusion 等），"
            "在生成的 LaTeX 中写出完整的 \\section{{...}} 框架，然后逐节填充正文。"
            if not has_sections
            else "基于第一步的分析，严格按模板框架填充正文。关键约束：\n"
                 "- **禁止修改框架**：不新增、删除、重排序或重命名任何章节，除非用户明确要求改结构"
        )

        draft_prompt = f"""
## 写作协议：先理解模板，再填充内容

### 第一步：分析模板（在脑中完成，不要输出）
1. 仔细阅读下面的「模板理解」和「模板源文摘录」，理解模板的文档类、包依赖、文档结构
2. 识别模板中已有的章节标题、顺序和每个章节的写作职责
3. 确认模板使用的引用命令、章节层级命令（\\chapter 还是 \\section）
4. 确认参考文献系统（biblatex 还是 bibtex）

### 第二步：生成内容
{structure_rule}
- **禁止重写模板**：documentclass、导言区、\\maketitle、参考文献尾部原样保留
- **引用命令沿用模板**：不要切换引用命令体系
- 不要输出 markdown，不要解释过程，只输出正文

---

项目快照：
{project_snapshot}

当前项目：
标题：{project.get("title", "")}
项目模式：{project.get("project_mode") or ("模板项目" if project.get("template_id") else "手动上传项目")}
写作类型：{project.get("writing_type", "")}
目标语言：{self._language_name(writing_language)}
项目目标：{project.get("goal", "")}
项目要求：{current_requirements}

用户本轮指令：
{message}

{structure_change_warning}

最近上下文记忆：
{recent_context_text}

代码工作区记忆：
{workspace_context_text}

当前项目结构与源码摘要：
{structure_digest}

上传材料理解：
{source_brief}

模板理解（必须严格遵守）：
{template_comprehension or "未检测到模板，请基于上传项目结构工作。"}

本地文献证据：
{evidence_text}

参考文献系统：
{bibliography_instruction}

要求：
- 你只能基于用户手动上传的项目结构、源码和材料工作，不要假设系统内置模板。
- 如果已经导入代码工作区，优先把代码实现、训练脚本、配置、结果图纳入写作上下文，并按"方法与实现 / 实验设计 / 结果与分析"使用它们。
- 若要在 LaTeX 中引用图片，应优先使用 `assets/workspace/...` 路径。
- 若上传项目中已有 .cls、.sty、章节拆分、bib 或图像资源，必须尽量沿用现有组织方式。
- 若上传材料已包含投稿要求、基金要求、模板规范、章节职责或任务说明，优先服从这些材料。
- Narrative text, section titles, abstract text, captions, and discussion must be written in {self._language_name(writing_language)}.
- If the target language is English, do not output Chinese prose unless the existing template contains fixed Chinese boilerplate that must be preserved.
- 如果引用本地文献，必须遵循上面的参考文献系统，优先使用模板已有命令，且只能使用上面提供的真实 citation key。
- 如果模板已存在引用命令，新增引用必须沿用同一命令；不要把原模板里的 \\citep / \\citet / \\parencite 等改写成标准 \\cite。
- 对现有项目，只允许改正文内容，不允许改 documentclass、导言区、标题、作者区和原有 bibliography 结构。
- 生成内容必须直接服务于用户写作，不要输出分析说明。
""".strip()
        try:
            raw = self._chat_completion(
                api_key=api_key,
                model=self._resolve_model_name(config, model_provider, "runner"),
                provider=model_provider,
                messages=[
                    {"role": "system", "content": self._template_guardian_prompt()},
                    {"role": "user", "content": draft_prompt},
                ],
                timeout=self._llm_timeout("draft", 240),
                retries=self._llm_retries("draft", 4),
            )
            body = re.sub(r"^```(?:latex|tex)?", "", raw.strip()).strip()
            body = re.sub(r"```$", "", body).strip()
        except Exception as exc:
            raise RuntimeError(f"正文生成失败：{exc}") from exc
        if not body.strip():
            raise RuntimeError("正文生成失败：模型未返回有效内容")
        invalid_markers = [
            "这里填写",
            "write the abstract here",
            "步骤一。",
            "创新点一。",
            "建议使用 bibtex",
        ]
        lowered_body = body.lower()
        if sum(1 for marker in invalid_markers if marker in lowered_body or marker in body) >= 2:
            raise RuntimeError("正文生成失败：模型返回了模板占位内容")
        bibliography = self._filter_bibtex_to_used_keys(
            self._library_cards_bibtex(citation_cards),
            body,
        )
        sync_payload = {
            "workspace_sections": [{"title": "Draft Body", "content": body}],
            "bibliography": bibliography,
            "bib_name": "reference.bib",
            "latex": body,
            "plan": {
                "writing_type": project.get("writing_type") or "academic",
                "writing_language": writing_language,
            },
        }
        sync_workflow_project(
            project_id,
            sync_payload,
            title=project.get("title") or "",
            goal=project.get("goal") or "",
            requirements=current_requirements,
            author=project.get("author") or author,
            query=source_query,
        )
        update_section_memory(project_id, project.get("main_tex") or "main.tex", body, prompt=message)

        # Audit → Revise → Compile loop
        audit_result = self._audit_and_revise_loop(
            project_id=project_id,
            api_key=api_key,
            model=self._resolve_model_name(config, model_provider, "runner"),
            provider=model_provider,
            template_profile=template_profile,
            current_requirements=current_requirements,
            source_query=source_query,
            author=project.get("author") or author,
            max_iterations=3,
        )
        compile_result = audit_result.get("final_compile") or compile_project(project_id)
        audit_summaries = audit_result.get("audit_summaries") or []
        final_verdict = audit_summaries[-1].get("verdict", "REVISE") if audit_summaries else "UNKNOWN"

        record_project_turn(
            project_id,
            "assistant",
            f"已生成主稿并通过审计（{final_verdict}），编译状态：{compile_result.get('status', 'unknown')}",
            kind="draft+audit",
            file_path=project.get("main_tex") or "main.tex",
            metadata={
                "compile_status": compile_result.get("status", ""),
                "evidence_count": len(citation_cards),
                "audit_iterations": len(audit_summaries),
                "audit_verdict": final_verdict,
            },
        )
        return {
            "status": "ok",
            "mode": "draft",
            "reply": f"已生成主稿并通过审计（{final_verdict}，{len(audit_summaries)} 轮修复），写入项目。",
            "project": load_project(project_id),
            "compile": compile_result,
            "audit": audit_summaries,
            "sources": load_project_sources(project_id, include_text=False),
            "evidence": [
                {
                    "title": item.get("title", ""),
                    "year": item.get("year", ""),
                    "venue": item.get("venue", ""),
                }
                for item in library_items[:8]
            ],
            "model_provider": model_provider,
        }

    def _audit_and_revise_loop(
        self,
        project_id: str,
        api_key: str,
        model: str,
        provider: str,
        template_profile: dict[str, Any],
        current_requirements: str,
        source_query: str,
        author: str,
        max_iterations: int = 3,
    ) -> dict[str, Any]:
        """Run audit→fix LLM call→sync→compile loop for up to max_iterations.

        Returns dict with audit_summary and final_compile.
        """
        summaries: list[dict[str, Any]] = []
        for iteration in range(1, max_iterations + 1):
            report = run_full_audit(
                project_id,
                profile=template_profile,
                api_key=api_key,
                model=model,
            )
            error_count = sum(1 for i in report.issues if i.severity == "error")
            warning_count = sum(1 for i in report.issues if i.severity == "warning")
            summaries.append({
                "iteration": iteration,
                "verdict": report.verdict,
                "issues": len(report.issues),
                "errors": error_count,
                "warnings": warning_count,
                "score": report.overall_score,
            })

            if report.verdict == "ACCEPT":
                break

            fix_prompt_text = audit_fix_prompt(report)
            if not fix_prompt_text or fix_prompt_text == "审计通过，无需修改。":
                break

            project = load_project(project_id)
            main_tex = project.get("main_tex") or "main.tex"
            try:
                current_content = str(read_project_file(project_id, main_tex).get("content") or "")
            except FileNotFoundError:
                current_content = ""

            # Extract body only — preserve preamble and end matter
            preamble, document_body, end_matter = "", current_content, ""
            body_start = current_content.find(r"\begin{document}")
            body_end = current_content.find(r"\end{document}")
            if body_start >= 0 and body_end >= 0:
                preamble = current_content[:body_start + len(r"\begin{document}")]
                document_body = current_content[body_start + len(r"\begin{document}"):body_end]
                end_matter = current_content[body_end:]
            # Strip bibliography tail from body — it belongs to end_matter
            bib_tail_start = document_body.rfind(r"\bibliographystyle")
            if bib_tail_start < 0:
                bib_tail_start = document_body.rfind(r"\bibliography")
            if bib_tail_start < 0:
                bib_tail_start = document_body.rfind(r"\printbibliography")
            if bib_tail_start >= 0:
                end_matter = document_body[bib_tail_start:] + end_matter
                document_body = document_body[:bib_tail_start]

            revise_prompt = f"""你是 LaTeX 正文修复助手。请根据审计报告仅修复**正文内容**的问题。
正文是 \\begin{{document}} 和参考文献区之间的内容。不要输出 preamble 或 \\end{{document}}。
不要输出 markdown 代码块或解释，只输出修复后的 LaTeX 正文。

{fix_prompt_text}

当前正文内容：
{document_body[:16000]}
"""
            try:
                raw = self._chat_completion(
                    api_key=api_key,
                    model=model,
                    provider=provider,
                    messages=[
                        {"role": "system", "content": "你只输出修复后的 LaTeX 正文片段。不要输出 preamble、\\begin{document}、\\end{document} 或 markdown。"},
                        {"role": "user", "content": revise_prompt},
                    ],
                    timeout=self._llm_timeout("revise", 300),
                    retries=self._llm_retries("revise", 3),
                )
            except Exception:
                break

            revised = re.sub(r"^```(?:latex|tex)?", "", raw.strip()).strip()
            revised = re.sub(r"```$", "", revised).strip()
            revised = re.sub(r"\\end\{document\}", "", revised)
            if not revised.strip():
                break

            # Reconstruct full file preserving preamble and end matter
            full_revised = preamble + "\n" + revised.strip() + "\n" + end_matter

            # Write fixed content back to project file
            from .writing_workspace import _project_file_path as _wpfp
            main_path = _wpfp(project_id, main_tex)
            main_path.write_text(full_revised, encoding="utf-8")

            template_profile = build_template_profile(project_id, template_id=str(project.get("template_id") or "")) or template_profile

        final_compile = compile_project(project_id)
        return {
            "audit_summaries": summaries,
            "final_iterations": len(summaries),
            "final_compile": final_compile,
        }

    def _handle_section_generation(self, config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload.get("project_id") or "").strip()
        file_path = str(payload.get("file_path") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        mode = str(payload.get("mode") or "section").strip() or "section"
        context = str(payload.get("context") or "")
        api_key = str(payload.get("api_key") or "").strip()
        model_provider = self._resolve_model_provider(payload)
        if not project_id:
            raise ValueError("project_id is required")
        if not file_path:
            raise ValueError("file_path is required")
        if not prompt:
            raise ValueError("prompt is required")

        project = load_project(project_id)
        project_context = load_project_context(
            project_id,
            file_path,
            include_source_text=False,
            recent_context_limit=8,
            section_memory_limit=6,
            evidence_card_limit=6,
            conversation_limit=12,
        )
        file_record = {}
        try:
            file_record = read_project_file(project_id, file_path)
        except FileNotFoundError:
            file_record = {"content": ""}
        current_content = context or str(file_record.get("content") or "")
        section_context = project_context.get("section") or {}
        recent_context_text = self._format_recent_context(project_context.get("recent_context") or [])
        section_memory_text = self._format_section_memories(project_context.get("section_memories") or [])
        evidence_memory_text = self._format_evidence_cards(project_context.get("evidence_memory") or {})
        source_files = project_context.get("source_files") or []
        workspace_context_text = self._format_workspace_focus(project_context.get("workspace_index") or {})
        source_brief = self._source_brief(source_files[:6])
        project_snapshot = self._format_project_snapshot(project_context, limit=6)
        writing_language = self._infer_writing_language(
            message=prompt,
            requirements=str(project.get("requirements", "") or ""),
            writing_type=str(project.get("writing_type", "") or "academic"),
            template_id=str(project.get("template_id") or ""),
            sources=source_files,
            project=project,
            explicit=str(payload.get("writing_language") or project.get("writing_language") or ""),
        )

        section_query = " ".join(
            filter(
                None,
                [
                    prompt,
                    file_path,
                    str(section_context.get("title") or ""),
                    project.get("goal", ""),
                    project.get("requirements", ""),
                ],
            )
        )[:320]
        library_items = search_library(
            config, section_query, limit=6,
            api_key=api_key, model_provider=model_provider,
        )
        evidence_text, citation_cards = self._format_library_evidence(library_items[:6])
        bibliography_instruction = self._bibliography_instruction(project)
        if not citation_cards:
            evidence_text = "暂无新增本地证据。"

        system_prompt = (
            self._template_guardian_prompt()
            + "\n\n"
            + (
                "输出格式：第一段是 1-3 句简短说明；"
                "最后单独一行输出 INSERT_TEXT: 后接完整 LaTeX 片段。"
            )
        )
        section_structure_warning = self._detect_structure_change_intent(prompt, str(project.get("requirements", "")))
        try:
            section_template_profile = project.get("template_profile") or build_template_profile(project_id, template_id=str(project.get("template_id") or ""))
        except Exception:
            section_template_profile = {}
        section_template_comprehension = template_comprehension_prompt(section_template_profile) if section_template_profile else ""

        user_prompt = f"""
## 写作协议：先理解项目模板，再写当前章节

### 第一步：确认模板约束（在脑中完成，不要输出）
1. 查看项目快照，确认模板中有哪些章节、各自职责是什么
2. 确认模板使用的引用命令和参考文献系统
3. 确认当前章节在整个项目中的位置和职责

### 第二步：生成当前章节内容
严格按模板框架填充。**禁止修改框架**，禁止重写模板结构。

---

项目标题：{project.get("title", "")}
写作类型：{project.get("writing_type", "")}
目标语言：{self._language_name(writing_language)}
当前文件：{file_path}
当前章节：{section_context.get("title", Path(file_path).stem)}
生成模式：{mode}

项目快照：
{project_snapshot}

模板理解（必须严格遵守）：
{section_template_comprehension or "未检测到模板结构信息。"}

项目目标：
{project.get("goal", "")}

严格要求：
{project.get("requirements", "")}

最近上下文记忆：
{recent_context_text}

章节历史记忆：
{section_memory_text}

证据记忆卡：
{evidence_memory_text}

代码工作区记忆：
{workspace_context_text}

上传材料摘录：
{source_brief}

当前文件内容：
{current_content[:5000] if current_content else "当前为空白章节。"}

本地证据：
{evidence_text}

参考文献系统：
{bibliography_instruction}

用户任务：
{prompt}

{section_structure_warning}

要求：
- 如果 mode 是 section，输出一个完整章节版本，允许覆盖当前文件。
- 如果 mode 是 continue，续写并保持与当前内容衔接。
- 如果 mode 是 revise，基于当前内容重写，使结构更符合要求。
- 必须严格服从项目要求，尤其是格式约束、章节职责和写作口吻。
- Narrative text, section titles, captions, and discussion must be written in {self._language_name(writing_language)}.
- 对于实验方案、实验设计、可行性分析、阶段进展这类章节，优先使用导入工作区里的代码文件、训练脚本、模型定义、预测脚本和结果图来组织内容。
- 如果工作区里存在 train.py、model.py、predict.py、label.py 等文件，优先据此描述实验流程、模型结构、训练设置和推理过程，不要泛泛而谈。
- 对于正式论文、综述、毕业论文、申报书，要把内容写完整，不要给提纲式空话。
- 必要时可使用 \\section{{}}、\\subsection{{}}、enumerate、itemize。
- 如果工作区中已有结果图，可以直接用 `assets/workspace/...` 引用。
- 若使用本地文献，引用必须遵循上面的参考文献系统，优先使用模板已有命令，且只能使用上面证据区给出的真实 citation key。
- 如果模板已存在引用命令，新增引用必须沿用同一命令；不要把原模板里的 \\citep / \\citet / \\parencite 等改写成标准 \\cite。
- 对现有项目，只允许改正文内容，不允许改 documentclass、导言区、标题、作者区和原有 bibliography 结构。
""".strip()

        raw = self._chat_completion(
            api_key=api_key,
            model=self._resolve_model_name(config, model_provider, "runner"),
            provider=model_provider,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout=self._llm_timeout("section", 300),
            retries=self._llm_retries("section", 4),
        )
        reply, insert_text = self._extract_insert_text(raw)
        final_text = insert_text.strip() or current_content
        # Filter out citation keys that don't exist in the project bibliography
        try:
            from .writing_workspace import _project_bibliography_keys, _remove_unknown_citations
            allowed_keys = _project_bibliography_keys(project_id)
            final_text = _remove_unknown_citations(
                final_text,
                allowed_keys,
                bibliography_profile=project.get("bibliography_profile") or {},
            )
        except Exception:
            pass
        bibliography = self._filter_bibtex_to_used_keys(
            self._library_cards_bibtex(citation_cards),
            final_text,
        )
        if bibliography.strip():
            merge_project_bibliography(
                project_id,
                bibliography,
                suggested_name="reference.bib",
                bibliography_profile=project.get("bibliography_profile") or {},
            )
        saved_file = save_project_file(
            {
                "project_id": project_id,
                "path": file_path,
                "content": final_text,
                "set_main_tex": bool(payload.get("set_main_tex", False)),
                "preserve_structure": True,
                "bibliography": bibliography,
            }
        )
        update_section_memory(
            project_id,
            file_path,
            final_text,
            prompt=prompt,
            evidence_keys=[str(item.get("key") or "") for item in citation_cards],
        )
        workflow_state = get_workflow_state(project_id)
        current_section_id = self._section_id_for_file(project_id, file_path, section_context)
        if current_section_id:
            from .writing_workflow import _ensure_state, _save_state

            state = _ensure_state(project_id)
            for item in state.get("sections", []) or []:
                if str(item.get("section_id") or "") == current_section_id:
                    item["last_guardrail_violations"] = (saved_file.get("guardrails") or {}).get("violations") or []
                    break
            _save_state(project_id, state)
            workflow_state = get_workflow_state(project_id)
        record_project_turn(project_id, "user", prompt, kind=f"section:{mode}", file_path=file_path)
        record_project_turn(
            project_id,
            "assistant",
            reply or final_text,
            kind=f"section:{mode}:reply",
            file_path=file_path,
            metadata={"evidence_count": len(citation_cards), "section_title": section_context.get("title", "")},
        )

        # Quick post-generation audit (Mode C + D) for the section file
        try:
            section_profile = project.get("template_profile") or build_template_profile(project_id, template_id=str(project.get("template_id") or ""))
        except Exception:
            section_profile = {}
        section_audit_issues: list[dict[str, Any]] = []
        if section_profile:
            from .writing_audit import run_latex_syntax_audit, run_citation_integrity_audit
            from .research_workflow import DEFAULT_OUTPUT_DIR
            section_project_dir = DEFAULT_OUTPUT_DIR / project_id
            section_audit_issues = [
                *run_latex_syntax_audit(project_id, section_profile, section_project_dir),
                *run_citation_integrity_audit(project_id, section_profile, section_project_dir),
            ]
        workflow = workflow_state
        current_section = workflow.get("current_section") or {}
        return {
            "status": "ok",
            "mode": mode,
            "reply": reply,
            "insert_text": final_text,
            "project": load_project(project_id),
            "context": load_project_context(project_id, file_path),
            "workflow": workflow,
            "pending_citations": current_section.get("pending_citations") or [],
            "guardrails": saved_file.get("guardrails") or {},
            "audit_issues": [
                {
                    "mode": i.mode,
                    "severity": i.severity,
                    "location": i.location,
                    "category": i.category,
                    "description": i.description,
                    "fix_suggestion": i.fix_suggestion,
                }
                for i in section_audit_issues
            ],
            "evidence": [
                {
                    "title": item.get("title", ""),
                    "year": item.get("year", ""),
                    "venue": item.get("venue", ""),
                }
                for item in library_items[:6]
            ],
            "raw": raw,
            "model_provider": model_provider,
        }

    def _handle_writing_audit(self, config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        api_key = str(payload.get("api_key") or "").strip()
        model = str(payload.get("model") or self._resolve_model_name(config, self._resolve_model_provider(payload), "runner"))
        project = load_project(project_id)
        template_profile = project.get("template_profile") or build_template_profile(project_id, template_id=str(project.get("template_id") or ""))
        report = run_full_audit(
            project_id,
            profile=template_profile,
            api_key=api_key,
            model=model,
        )
        record_project_turn(
            project_id,
            "user",
            "手动触发写作审计",
            kind="audit",
            metadata={"verdict": report.verdict, "issue_count": len(report.issues)},
        )
        return {
            "status": "ok",
            "report": {
                "project_id": report.project_id,
                "version": report.version,
                "verdict": report.verdict,
                "issues": [
                    {
                        "mode": i.mode,
                        "severity": i.severity,
                        "location": i.location,
                        "category": i.category,
                        "description": i.description,
                        "fix_suggestion": i.fix_suggestion,
                    }
                    for i in report.issues
                ],
                "scores": report.scores,
                "overall_score": report.overall_score,
                "error_count": sum(1 for i in report.issues if i.severity == "error"),
                "warning_count": sum(1 for i in report.issues if i.severity == "warning"),
                "info_count": sum(1 for i in report.issues if i.severity == "info"),
            },
            "fix_prompt": audit_fix_prompt(report),
        }

    def _handle_writing_audit_fix(self, config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        api_key = str(payload.get("api_key") or "").strip()
        model_provider = self._resolve_model_provider(payload)
        model = self._resolve_model_name(config, model_provider, "runner")
        max_iterations = int(payload.get("max_iterations", 3))

        project = load_project(project_id)
        try:
            template_profile = project.get("template_profile") or build_template_profile(project_id, template_id=str(project.get("template_id") or ""))
        except Exception:
            template_profile = {}

        result = self._audit_and_revise_loop(
            project_id=project_id,
            api_key=api_key,
            model=model,
            provider=model_provider,
            template_profile=template_profile,
            current_requirements=project.get("requirements") or "",
            source_query=project.get("goal") or "",
            author=project.get("author") or "Scientific Agent",
            max_iterations=max_iterations,
        )
        audit_summaries = result.get("audit_summaries") or []
        final_compile = result.get("final_compile") or {}
        final_verdict = audit_summaries[-1].get("verdict", "REVISE") if audit_summaries else "UNKNOWN"
        record_project_turn(
            project_id,
            "user",
            f"运行审计修复循环（{len(audit_summaries)} 轮，判定：{final_verdict}）",
            kind="audit:fix",
            metadata={"iterations": len(audit_summaries), "verdict": final_verdict},
        )
        return {
            "status": "ok",
            "final_verdict": final_verdict,
            "iterations": len(audit_summaries),
            "summaries": audit_summaries,
            "compile": final_compile,
            "project": load_project(project_id),
        }

    def _chat_completion(
        self,
        api_key: str,
        model: str,
        provider: str,
        messages: list[dict[str, str]],
        timeout: int = 90,
        retries: int = 3,
    ) -> str:
        secret = load_provider_api_key(provider, api_key)
        if not secret:
            raise ValueError(f"Missing {provider_label(provider)} API key.")

        from urllib import error, request

        body: dict[str, Any] = {}
        attempts = max(1, int(retries or 1))
        for attempt in range(1, attempts + 1):
            req = request.Request(
                f"{provider_api_base(provider)}/chat/completions",
                data=json.dumps({"model": model, "messages": messages}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {secret}",
                },
                method="POST",
            )
            try:
                with request.urlopen(req, timeout=timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                if exc.code not in {408, 409, 429, 500, 502, 503, 504} or attempt == attempts:
                    raise RuntimeError(f"{provider_label(provider)} API error: {exc.code} {detail}") from exc
                time.sleep(min(30, 3 * attempt * attempt))
            except socket.timeout as exc:
                if attempt == attempts:
                    raise RuntimeError(f"{provider_label(provider)} read timeout after {timeout}s") from exc
                time.sleep(min(30, 3 * attempt * attempt))
            except TimeoutError as exc:
                if attempt == attempts:
                    raise RuntimeError(f"{provider_label(provider)} timeout after {timeout}s") from exc
                time.sleep(min(30, 3 * attempt * attempt))
            except error.URLError as exc:
                if attempt == attempts:
                    raise RuntimeError(f"{provider_label(provider)} network error: {exc}") from exc
                time.sleep(min(30, 3 * attempt * attempt))
            except Exception as exc:
                if attempt == attempts:
                    raise RuntimeError(f"{provider_label(provider)} unexpected error: {exc}") from exc
                time.sleep(min(30, 3 * attempt * attempt))

        return (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

    def _template_guardian_prompt(self) -> str:
        return (
            "你是学术写作智能体，核心身份是「模板守护者」。\n"
            "\n"
            "你必须严格遵循以下规则，违反即为严重错误：\n"
            "\n"
            "1. **模板结构不可侵犯**：绝不修改 documentclass、导言区、\\maketitle 区域、"
            "参考文献尾部结构。绝不新增、删除、重排序或重命名章节。"
            "仅当用户在本轮对话中明确要求「增加章节」「删除章节」「调整结构」「修改框架」时才可变更。\n"
            "2. **引用系统不可切换**：必须沿用模板已有引用命令（\\citep/\\citet/\\parencite/\\autocite 等），"
            "不得擅自改为 \\cite。模板用 biblatex 则保持 biblatex，用 bibtex 则保持 bibtex。\n"
            "3. **章节层级必须匹配**：模板用 \\chapter 则输出 \\chapter，模板用 \\section 则输出 \\section，"
            "不得自行改变层级。\n"
            "4. **只输出可编译正文**：输出放在 \\begin{document} 之后、参考文献区之前的 LaTeX 内容，"
            "不输出 markdown 代码块。\n"
            "5. **去AI味、学术规范**：使用具体、有信息量的学术语言。禁止空洞套话（如 \"It is worth noting that\"、"
            "\"Furthermore\" 机械堆砌、\"in this paper we\" 泛滥），禁止无实质内容的过渡句。"
            "每句话应承载具体信息：定义问题、引用证据、比较方法、指出差距、提出路线、分析风险。\n"
            "6. **先读懂模板再写**：收到任务后，先识别模板中已有的所有章节标题和顺序，"
            "确认每个章节的写作职责，然后严格按照该框架填充内容。"
        )

    def _extract_insert_text(self, raw: str) -> tuple[str, str]:
        if "INSERT_TEXT:" not in raw:
            cleaned = raw.strip()
            return cleaned, cleaned
        natural, insert_text = raw.split("INSERT_TEXT:", 1)
        reply = natural.strip()
        insert_body = re.sub(r"^```(?:latex)?", "", insert_text.strip()).strip()
        insert_body = re.sub(r"```$", "", insert_body).strip()
        return reply, insert_body

    def _send_file(self, relative_path: str, content_type: str) -> None:
        project_root = Path(__file__).resolve().parents[2]
        file_path = project_root / relative_path
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main(host: str | None = None, port: int | None = None) -> None:
    host = host or os.environ.get("LIT_AGENT_HOST", "127.0.0.1")
    port = port if port is not None else int(os.environ.get("LIT_AGENT_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), CaptureHandler)
    print(f"Scientific Agent capture service listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
