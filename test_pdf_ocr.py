"""测试 PDF 解析 + 索引 + 检索全链路

测试场景:
1. 生成原生 PDF（有文本层）
2. 生成扫描版 PDF（图片型，无文本层）
3. 索引两个 PDF
4. 检索验证：原生 PDF 内容可检索
5. 检索验证：扫描版 PDF 通过 OCR 后可检索
"""
import sys
import asyncio
from pathlib import Path

# 添加 src 到 path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import fitz  # PyMuPDF


def create_native_pdf(path: Path):
    """生成原生 PDF（有文本层）"""
    doc = fitz.open()
    page = doc.new_page()
    title = "Attention Is All You Need"
    authors = "Vaswani, A., Shazeer, N., Parmar, N., et al."
    abstract = (
        "The dominant sequence transduction models are based on complex recurrent or "
        "convolutional neural networks that include an encoder and a decoder. "
        "We propose a new simple network architecture, the Transformer, based solely "
        "on attention mechanisms, dispensing with recurrence and convolutions entirely."
    )
    doi = "10.48550/arXiv.1706.03762"
    year = "2017"
    text = f"{title}\n{authors}\n\nAbstract: {abstract}\n\nDOI: {doi}\nYear: {year}"
    page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    print(f"[OK] 原生 PDF 已生成: {path}")


def create_scanned_pdf(path: Path):
    """生成扫描版 PDF（图片型，无文本层）

    通过创建一个带文字的图片，然后将图片插入 PDF，使其没有文本层。
    """
    import numpy as np
    try:
        import cv2
    except ImportError:
        print("[WARN] opencv 未安装，尝试用 fitz 生成图片型 PDF")
        # 兜底：用 fitz 画一些形状模拟扫描版（无文字文本层）
        doc = fitz.open()
        page = doc.new_page()
        # 画一些内容但不插入文本（模拟扫描版无文本层）
        rect = fitz.Rect(72, 72, 500, 200)
        page.draw_rect(rect, color=(0, 0, 0), width=1)
        doc.save(str(path))
        doc.close()
        print(f"[OK] 扫描版 PDF 已生成（简单版）: {path}")
        return

    # 用 opencv 生成带文字的图片
    img = np.ones((800, 600, 3), dtype=np.uint8) * 255
    title = "BERT: Pre-training of Deep Bidirectional Transformers"
    authors = "Devlin, J., Chang, M., Lee, K., Toutanova, K."
    abstract = (
        "We introduce a new language representation model called BERT, which stands "
        "for Bidirectional Encoder Representations from Transformers. Unlike recent "
        "language representation models, BERT is designed to pre-train deep bidirectional "
        "representations from unlabeled text by jointly conditioning on both left and "
        "right context in all layers."
    )
    cv2.putText(img, title, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    cv2.putText(img, authors, (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    for i, line in enumerate(abstract.split(". ")[:5]):
        cv2.putText(img, line, (20, 140 + i * 30), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)

    # 将图片插入 PDF
    doc = fitz.open()
    page = doc.new_page()
    img_bytes = cv2.imencode(".png", img)[1].tobytes()
    page.insert_image(fitz.Rect(0, 0, 612, 792), stream=img_bytes)
    doc.save(str(path))
    doc.close()
    print(f"[OK] 扫描版 PDF 已生成: {path}")


def test_extract_pdf_text(workspace: Path):
    """测试 PDF 文本提取（含 OCR）"""
    from sage.context.index import ProjectIndex
    from sage.memory.store import MemoryStore

    store = MemoryStore()
    indexer = ProjectIndex(workspace, store)

    print("\n=== 测试 1: 原生 PDF 文本提取 ===")
    native_pdf = workspace / "native.pdf"
    text = indexer._extract_pdf_text(native_pdf)
    print(f"提取文本长度: {len(text)}")
    print(f"前 200 字符:\n{text[:200]}")
    assert "Transformer" in text or "attention" in text.lower(), "原生 PDF 文本提取失败"
    print("[PASS] 原生 PDF 文本提取正常")

    print("\n=== 测试 2: 扫描版 PDF 文本提取（含 OCR）===")
    scanned_pdf = workspace / "scanned.pdf"
    text = indexer._extract_pdf_text(scanned_pdf)
    print(f"提取文本长度: {len(text)}")
    print(f"前 300 字符:\n{text[:300]}")
    # OCR 识别结果可能不完美，但应包含 BERT 或 Bidirectional
    assert "BERT" in text or "Bidirectional" in text or "Pre" in text, "扫描版 PDF OCR 识别失败"
    print("[PASS] 扫描版 PDF OCR 识别正常")


def test_index_and_search(workspace: Path):
    """测试索引 + 检索全链路"""
    from sage.context.index import ProjectIndex
    from sage.memory.store import MemoryStore

    print("\n=== 测试 3: 索引工作空间 ===")
    store = MemoryStore()
    indexer = ProjectIndex(workspace, store)
    stats = indexer.index_project(force=True)
    print(f"索引结果: {stats}")
    assert stats["files"] >= 2, f"索引文件数不足: {stats}"
    assert stats["chunks"] > 0, "索引 chunk 数为 0"
    print("[PASS] 索引完成")

    print("\n=== 测试 4: 检索原生 PDF 内容 ===")
    results = indexer.search("Transformer attention mechanism", top_k=3, threshold=0.2, rerank=False)
    print(f"检索结果数: {len(results)}")
    for i, r in enumerate(results, 1):
        print(f"  结果 {i}: file={r.file_path} score={r.score:.3f} title={r.title}")
        print(f"    content 前 100 字符: {r.content[:100]}")
    assert len(results) > 0, "检索原生 PDF 内容无结果"
    print("[PASS] 原生 PDF 检索正常")

    print("\n=== 测试 5: 检索扫描版 PDF 内容（OCR 后）===")
    results = indexer.search("BERT bidirectional encoder", top_k=3, threshold=0.2, rerank=False)
    print(f"检索结果数: {len(results)}")
    for i, r in enumerate(results, 1):
        print(f"  结果 {i}: file={r.file_path} score={r.score:.3f} title={r.title}")
        print(f"    content 前 100 字符: {r.content[:100]}")
    # OCR 结果可能质量稍低，用较低阈值
    assert len(results) > 0, "检索扫描版 PDF 内容无结果"
    print("[PASS] 扫描版 PDF OCR 后检索正常")

    print("\n=== 测试 6: 元数据提取 ===")
    for r in results:
        if r.title or r.authors or r.year or r.doi:
            print(f"  元数据: title={r.title} authors={r.authors} year={r.year} doi={r.doi}")
            print("  [PASS] 元数据提取正常")
            break
    else:
        print("  [WARN] 未提取到元数据（可能 OCR 文本质量影响正则匹配）")


async def test_ocr_document_tool(workspace: Path):
    """测试 ocr_document 工具"""
    from sage.tools.paper_ops import PaperOps

    print("\n=== 测试 7: ocr_document 工具 ===")
    ops = PaperOps(workspace)
    result = await ops.ocr_document("scanned.pdf")
    print(f"工具调用结果: success={result.success}")
    print(f"output 前 200 字符: {result.output[:200] if result.output else ''}")
    print(f"data: {result.data}")
    assert result.success, "ocr_document 工具调用失败"
    assert result.data and result.data.get("ocr_pages", 0) > 0, "未识别到 OCR 页面"
    print("[PASS] ocr_document 工具正常")


def main():
    workspace = Path(__file__).parent / "test_workspace"
    workspace.mkdir(exist_ok=True)

    print("=" * 60)
    print("Sage PDF 解析 + 索引 + 检索全链路测试")
    print("=" * 60)

    # 1. 生成测试 PDF
    print("\n--- 步骤 1: 生成测试 PDF ---")
    create_native_pdf(workspace / "native.pdf")
    create_scanned_pdf(workspace / "scanned.pdf")

    # 2. 测试文本提取
    print("\n--- 步骤 2: 测试 PDF 文本提取 ---")
    test_extract_pdf_text(workspace)

    # 3. 测试索引 + 检索
    print("\n--- 步骤 3: 测试索引 + 检索 ---")
    test_index_and_search(workspace)

    # 4. 测试 ocr_document 工具
    print("\n--- 步骤 4: 测试 ocr_document 工具 ---")
    asyncio.run(test_ocr_document_tool(workspace))

    print("\n" + "=" * 60)
    print("[全部测试通过] PDF 解析 + OCR + 索引 + 检索链路正常")
    print("=" * 60)

    # 清理测试工作空间
    import shutil
    shutil.rmtree(workspace, ignore_errors=True)
    # 清理测试 memory.db
    test_db = Path(__file__).parent / "memory.db"
    if test_db.exists():
        test_db.unlink()


if __name__ == "__main__":
    main()
