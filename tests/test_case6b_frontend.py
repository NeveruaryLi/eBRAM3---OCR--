import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Case6BFrontendContractTests(unittest.TestCase):
    def test_case6b_page_and_assets_are_wired(self):
        app_source = (ROOT / "app.py").read_text("utf-8")
        index = (ROOT / "static" / "index.html").read_text("utf-8")
        html = (ROOT / "static" / "case6b.html").read_text("utf-8")
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        css = (ROOT / "static" / "case6b.css").read_text("utf-8")

        self.assertIn('@app.get("/case6b")', app_source)
        self.assertIn('href="/case6b"', index)
        self.assertIn("服务协议草案生成", html)
        self.assertIn("template_file", javascript)
        self.assertIn("material_files", javascript)
        self.assertNotIn("form_entries", javascript)
        self.assertNotIn("formEntriesInput", html)
        self.assertIn("removeTemplate", javascript)
        self.assertIn("removeMaterial", javascript)
        self.assertIn("addMaterials", javascript)
        self.assertIn("state.busy || Boolean(state.sessionId)", javascript)
        self.assertIn("if (!response.ok)", javascript)
        self.assertIn("history-title-button", javascript)
        self.assertIn("title.type = 'button'", javascript)
        self.assertIn("syncUploadControls", javascript)
        self.assertIn("clearView(false);", javascript)
        self.assertIn("conflict_resolutions", javascript)
        self.assertIn("accepted_field_ids", javascript)
        self.assertNotIn("createPreview", javascript)
        self.assertIn("/document-view", javascript)
        self.assertIn("/document-shell", javascript)
        self.assertIn("scheduleAutoSave", javascript)
        self.assertIn("AbortController", javascript)
        self.assertIn("/cancel", javascript)
        self.assertIn("setReviewDirty", javascript)
        self.assertIn("optional", javascript)
        self.assertIn("/case6b/session/", javascript)
        self.assertIn("ebram_c6b_history", javascript)
        self.assertIn("#5d6f91", css.lower())

    def test_page_has_accessible_workflow_controls(self):
        html = (ROOT / "static" / "case6b.html").read_text("utf-8")
        self.assertIn('id="templateInput"', html)
        self.assertIn('id="materialsInput"', html)
        self.assertIn('id="templatePreflight"', html)
        self.assertNotIn('id="templateConfirmBtn"', html)
        self.assertIn('id="conflictPanel"', html)
        self.assertNotIn('id="previewFrame"', html)
        self.assertIn('id="documentCanvas"', html)
        self.assertIn('id="documentMode"', html)
        self.assertIn('id="differenceToggle"', html)
        self.assertIn('id="cancelAnalysisBtn"', html)
        self.assertIn('id="dirtyBadge"', html)
        self.assertNotIn('保存并刷新预览', html)
        self.assertIn('自动保存', html)
        self.assertIn('生成正式文档', html)
        self.assertIn('id="resetTaskBtn"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertNotIn('id="chatInput"', html)

    def test_docx_renderer_explicitly_disables_alt_chunks(self):
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")

        self.assertIn("renderAltChunks: false", javascript)

    def test_autosave_response_is_scoped_to_the_active_review_context(self):
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        flush_source = javascript.split(
            "async function flushAutoSave()", 1
        )[1].split("async function renderReview()", 1)[0]
        clear_source = javascript.split(
            "function clearView(updateCurrent)", 1
        )[1].split("function newTask()", 1)[0]

        self.assertIn("reviewContextToken: 0", javascript)
        self.assertIn("function captureReviewContext()", javascript)
        self.assertIn("function isCurrentReviewContext(context)", javascript)
        self.assertIn("const saveContext = captureReviewContext();", flush_source)
        self.assertIn("encodeURIComponent(saveContext.sessionId)", flush_source)
        self.assertGreaterEqual(
            flush_source.count("isCurrentReviewContext(saveContext)"),
            3,
        )
        self.assertIn("invalidateReviewContext();", clear_source)

    def test_service_suggestions_only_use_group_level_updates(self):
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        evidence_source = javascript.split(
            "function openEvidence(fieldId)", 1
        )[1].split("function updatePendingNavigation()", 1)[0]
        collect_source = javascript.split(
            "function collectReview()", 1
        )[1].split("function applySavedReview(", 1)[0]

        self.assertIn(
            "field.can_confirm && field.group_key !== 'services'",
            evidence_source,
        )
        self.assertIn(
            "reviewField(fieldId)?.can_confirm",
            collect_source,
        )
        self.assertIn(
            "reviewField(fieldId)?.group_key !== 'services'",
            collect_source,
        )

    def test_cancellation_stays_bound_to_its_analysis_run_until_completion(self):
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        run_source = javascript.split(
            "async function runAnalysis(url, options)", 1
        )[1].split("function renderTemplatePreflight(", 1)[0]
        cancel_source = javascript.split(
            "async function cancelAnalysis()", 1
        )[1].split("function showError(", 1)[0]

        self.assertIn("analysisRunToken: null", javascript)
        self.assertIn("cancelRunToken: null", javascript)
        self.assertIn("analysisCompletion: null", javascript)
        self.assertIn("const runToken = Symbol(", run_source)
        self.assertIn("analysisCancellationRequested(runToken)", run_source)
        self.assertNotIn("state.analysisRunToken = null", run_source)
        self.assertIn("state.cancelRunToken = runToken", cancel_source)
        self.assertIn("const controller = state.analysisController", cancel_source)
        self.assertIn("controller.abort();", cancel_source)
        self.assertIn("await completion;", cancel_source)
        self.assertIn("state.analysisRunToken !== runToken", cancel_source)
        self.assertIn("state.analysisRunToken = null", cancel_source)
        self.assertNotIn("state.cancelRequested = false", cancel_source)

    def test_document_render_is_scoped_and_committed_atomically(self):
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        render_source = javascript.split(
            "async function renderDocumentEditor(reviewContext)", 1
        )[1].split("function renderFallbackFields(", 1)[0]

        self.assertGreaterEqual(
            render_source.count("isCurrentReviewContext(reviewContext)"),
            5,
        )
        self.assertIn("const renderHost = document.createElement('div')", render_source)
        self.assertIn("renderAsync(shell, renderHost, renderHost", render_source)
        self.assertIn(
            "els.documentCanvas.replaceChildren(...Array.from(renderHost.childNodes))",
            render_source,
        )
        self.assertIn("await renderReview(reviewContext)", javascript)

    def test_navigation_flushes_unsaved_review(self):
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        navigation_source = javascript.split(
            "async function saveBeforeNavigation()", 1
        )[1].split("async function switchHistory(", 1)[0]
        switch_source = javascript.split(
            "async function switchHistory(item)", 1
        )[1].split("function setTemplate(", 1)[0]
        new_task_source = javascript.split(
            "async function newTask()", 1
        )[1].split("function closeSidebar(", 1)[0]

        self.assertIn("await flushAutoSave()", navigation_source)
        self.assertIn("await saveBeforeNavigation()", switch_source)
        self.assertIn("await saveBeforeNavigation()", new_task_source)

    def test_conflict_controls_are_locked_during_save(self):
        javascript = (ROOT / "static" / "case6b.js").read_text("utf-8")
        controls_source = javascript.split(
            "function syncReviewControls()", 1
        )[1].split("function setSaveStatus(", 1)[0]

        self.assertIn(
            "els.conflictList?.querySelectorAll('select')",
            controls_source,
        )
        self.assertIn(
            "control.disabled = state.busy || state.saveInFlight",
            controls_source,
        )


if __name__ == "__main__":
    unittest.main()
