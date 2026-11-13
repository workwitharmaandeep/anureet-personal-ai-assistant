import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import server
from agent_core import LocalAIAgent
from config import config
from scripts import telegram_bot
from tools import deep_research_tools, file_tools, premiere_tools, utility_tools
from voice_assistant import AnureetVoiceEngine


class TestServerAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)

    def test_health_endpoint(self):
        with patch.object(server.agent, "check_ollama_status", return_value=False):
            resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["ollama_online"])

    def test_find_available_port_skips_occupied_port(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            busy_port = blocker.getsockname()[1]
            resolved = server.find_available_port("127.0.0.1", busy_port, max_offset=10)
            self.assertGreater(resolved, busy_port)

    def test_chat_empty_query_rejected(self):
        resp = self.client.post("/api/chat", json={"query": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_chat_weather_fast_route(self):
        mock_weather = {
            "status": "success",
            "summary": "Weather in Kurukshetra, India: 26°C, Clear sky.",
        }
        with patch("agent_core.get_weather", return_value=mock_weather):
            resp = self.client.post("/api/chat", json={"query": "what is today's weather in Kurukshetra?"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("26°C", data["response_text"])
        self.assertEqual(data["executed_tools"][0]["tool"], "get_weather")
        self.assertIn("execution_time_ms", data["executed_tools"][0])

    def test_browse_files_posix_and_size_formatted(self):
        resp = self.client.get("/api/files/browse?path=tools")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["current_path"], "tools")
        self.assertEqual(data["parent_path"], ".")
        self.assertTrue(any(item["path"] == "tools/os_tools.py" for item in data["items"]))
        file_item = next(item for item in data["items"] if not item["is_dir"])
        self.assertIn("size_formatted", file_item)
        self.assertNotIn("\\", file_item["path"])

    def test_browse_files_nonexistent_returns_404(self):
        resp = self.client.get("/api/files/browse?path=does_not_exist_xyz")
        self.assertEqual(resp.status_code, 404)

    def test_path_traversal_blocked(self):
        resp_browse = self.client.get("/api/files/browse?path=../../Windows")
        self.assertEqual(resp_browse.status_code, 403)

        resp_dl = self.client.get("/api/files/download?path=../../Windows/win.ini")
        self.assertEqual(resp_dl.status_code, 403)

    def test_upload_and_download_sanitized(self):
        resp = self.client.post(
            "/api/files/upload",
            data={"target_dir": "uploads"},
            files={"file": ("../../evil_test_upload.txt", b"hello-anureet", "text/plain")},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["filename"], "evil_test_upload.txt")
        self.assertEqual(payload["saved_path"], "uploads/evil_test_upload.txt")

        saved = config.uploads_dir / "evil_test_upload.txt"
        self.assertTrue(saved.exists())

        try:
            dl_path = self.client.get("/api/files/download?path=uploads/evil_test_upload.txt")
            self.assertEqual(dl_path.status_code, 200)
            self.assertEqual(dl_path.content, b"hello-anureet")

            # Also verify legacy file_path param works
            dl_alias = self.client.get("/api/files/download?file_path=uploads/evil_test_upload.txt")
            self.assertEqual(dl_alias.status_code, 200)
        finally:
            if saved.exists():
                saved.unlink()

    def test_websocket_chat_streams_status_and_response(self):
        with patch("agent_core.get_exchange_rate", return_value={"status": "success", "summary": "1 USD = 85.0000 INR"}):
            with self.client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"query": "USD to INR exchange rate"}))
                frames = []
                for _ in range(5):
                    frame = json.loads(ws.receive_text())
                    frames.append(frame)
                    if frame.get("type") == "response":
                        break
                types = [f["type"] for f in frames]
                self.assertIn("status", types)
                self.assertIn("response", types)
                resp_frame = next(f for f in frames if f["type"] == "response")
                self.assertIn("85.0000 INR", resp_frame["data"]["response_text"])


class TestAgentCore(unittest.TestCase):
    def setUp(self):
        self.test_agent = LocalAIAgent(ollama_url="http://localhost:11434", model_name="test-model")

    def test_fl_studio_route_does_not_match_open_file(self):
        with patch("agent_core.open_application") as mock_open:
            matched = self.test_agent._match_fast_route("open file explorer")
            self.assertIsNotNone(matched)
            mock_open.assert_called_with("file explorer")

        with patch("agent_core.open_application") as mock_open:
            matched_netflix = self.test_agent._match_fast_route("open netflix")
            self.assertIsNone(matched_netflix)
            mock_open.assert_not_called()

    def test_embedded_json_tool_call_supports_nested_objects(self):
        text = 'Sure:\n{"name": "list_files", "parameters": {"directory_path": ".", "nested": {"a": 1}}}'
        parsed = self.test_agent._parse_embedded_json_tool_call(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "list_files")
        self.assertEqual(parsed["args"]["nested"]["a"], 1)

    def test_ollama_string_arguments_handled(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_exchange_rate",
                                "arguments": '{"source_currency": "USD", "target_currency": "INR"}',
                            }
                        }
                    ],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": "1 USD is 85 INR.",
                }
            },
        ]
        with patch.object(self.test_agent._session, "post", return_value=mock_resp), patch.dict(
            self.test_agent.tools,
            {"get_exchange_rate": self.test_agent.tools["get_exchange_rate"].model_copy(
                update={"func": lambda source_currency="USD", target_currency="INR": {"summary": "1 USD = 85 INR"}}
            )},
        ):
            res = self.test_agent.execute("please check currency conversion rate using tool")
        self.assertEqual(res.response, "1 USD is 85 INR.")
        self.assertEqual(len(res.tool_calls_made), 1)
        self.assertEqual(res.tool_calls_made[0]["tool"], "get_exchange_rate")


class TestToolsAndVoice(unittest.TestCase):
    def test_voice_extract_wake_command(self):
        wake, cmd = AnureetVoiceEngine.extract_wake_command("Hey Anureet open notepad", 0.0)
        self.assertTrue(wake)
        self.assertEqual(cmd, "open notepad")

        wake_split, cmd_split = AnureetVoiceEngine.extract_wake_command("hey anu reet open chrome", 0.0)
        self.assertTrue(wake_split)
        self.assertEqual(cmd_split, "open chrome")

        wake_fuzzy, cmd_fuzzy = AnureetVoiceEngine.extract_wake_command("Hey onurit what is the weather", 0.0)
        self.assertTrue(wake_fuzzy)
        self.assertEqual(cmd_fuzzy, "what is the weather")

        wake_hi, cmd_hi = AnureetVoiceEngine.extract_wake_command("अनुरीत नोटपैड खोलो", 0.0)
        self.assertTrue(wake_hi)
        self.assertEqual(cmd_hi, "नोटपैड खोलो")

        false_wake, _ = AnureetVoiceEngine.extract_wake_command("we are going to read a book", 0.0)
        self.assertFalse(false_wake)

        wake_only, cmd_only = AnureetVoiceEngine.extract_wake_command("Anureet", 0.0)
        self.assertTrue(wake_only)
        self.assertEqual(cmd_only, "")

        follow, follow_cmd = AnureetVoiceEngine.extract_wake_command("what time is it", active_session_until=9999999999.0, now=100.0)
        self.assertTrue(follow)
        self.assertEqual(follow_cmd, "what time is it")

        # Strict mode (default): NO execution without 'Anureet'
        strict_wake, _ = AnureetVoiceEngine.extract_wake_command("open notepad", continuous_mode=False)
        self.assertFalse(strict_wake)

        strict_with_wake, strict_cmd = AnureetVoiceEngine.extract_wake_command("Anureet open notepad", continuous_mode=False)
        self.assertTrue(strict_with_wake)
        self.assertEqual(strict_cmd, "open notepad")

        # Continuous mode: executes without 'Anureet'
        cont_wake, cont_cmd = AnureetVoiceEngine.extract_wake_command("open notepad", continuous_mode=True)
        self.assertTrue(cont_wake)
        self.assertEqual(cont_cmd, "open notepad")

    def test_voice_continuous_mode_lifecycle(self):
        engine = AnureetVoiceEngine(init_stt=False)
        self.assertFalse(engine.is_continuous_mode_active())

        # Start continuous mode for 60s
        engine.start_continuous_mode(60.0)
        self.assertTrue(engine.is_continuous_mode_active())

        # An utterance without wake word should be accepted in continuous mode
        wake, cmd = engine.extract_wake_command("explain gravity", continuous_mode=engine.is_continuous_mode_active())
        self.assertTrue(wake)
        self.assertEqual(cmd, "explain gravity")

        # Stop continuous mode -> strict mode
        engine.stop_continuous_mode()
        self.assertFalse(engine.is_continuous_mode_active())

        # Now utterance without wake word must be rejected
        wake, cmd = engine.extract_wake_command("explain gravity", continuous_mode=engine.is_continuous_mode_active())
        self.assertFalse(wake)

        # But with wake word it is accepted
        wake, cmd = engine.extract_wake_command("Anureet explain gravity", continuous_mode=engine.is_continuous_mode_active())
        self.assertTrue(wake)
        self.assertEqual(cmd, "explain gravity")

    def test_continuous_chat_fast_routes(self):
        import agent_core
        agent = agent_core.LocalAIAgent()

        res_start = agent.process_query("listen continuously")
        self.assertIn("continuous listening mode activated", res_start.get("response_text", "").lower())

        res_start_hinglish = agent.process_query("baat karte raho")
        self.assertIn("continuous listening mode activated", res_start_hinglish.get("response_text", "").lower())

        res_stop = agent.process_query("stop continuous mode")
        self.assertIn("continuous chat mode ended", res_stop.get("response_text", "").lower())

        res_strict = agent.process_query("strict command mode")
        self.assertIn("continuous chat mode ended", res_strict.get("response_text", "").lower())

    def test_hinglish_fast_route_matches_user_language(self):
        test_agent = LocalAIAgent(ollama_url="http://localhost:11434", model_name="test-model")
        mock_weather = {
            "status": "success",
            "city": "Kurukshetra",
            "temperature_celsius": 27,
            "condition": "Clear sky",
            "wind_speed_kmh": 8,
            "max_temperature_today": 30,
            "min_temperature_today": 18,
            "summary": "Weather in Kurukshetra: 27°C",
        }
        with patch("agent_core.get_weather", return_value=mock_weather):
            res = test_agent.execute("aaj Kurukshetra ka weather kya hai?")
        self.assertIn("ka abhi ka mausam", res.response)

    def test_whisper_transcribe_passes_multilingual_and_bias_options(self):
        engine = object.__new__(AnureetVoiceEngine)
        mock_model = MagicMock()
        mock_seg = MagicMock()
        mock_seg.text = " Anureet open notepad "
        mock_model.transcribe.return_value = ([mock_seg], None)
        engine.whisper_model = mock_model

        text = engine._transcribe_whisper(b"RIFF0000WAVE")
        self.assertEqual(text, "Anureet open notepad")
        _, kwargs = mock_model.transcribe.call_args
        self.assertIsNone(kwargs["language"])
        self.assertFalse(kwargs["multilingual"])
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertTrue(kwargs["vad_filter"])
        self.assertFalse(kwargs["condition_on_previous_text"])
        self.assertIn("Anureet", kwargs["hotwords"])
        self.assertIn("Anureet", kwargs["initial_prompt"])
        self.assertIn("notepad kholo", kwargs["initial_prompt"])

    def test_whisper_transcribe_skips_silent_wav_via_vad(self):
        engine = object.__new__(AnureetVoiceEngine)
        mock_model = MagicMock()
        engine.whisper_model = mock_model
        silent = AnureetVoiceEngine._silent_wav_bytes(duration_ms=500)

        text = engine._transcribe_whisper(silent)
        self.assertEqual(text, "")
        mock_model.transcribe.assert_not_called()

    def test_agent_reports_clear_ollama_offline_message(self):
        import requests
        test_agent = LocalAIAgent(ollama_url="http://localhost:11434", model_name="test-model")
        with patch.object(test_agent._session, "post", side_effect=requests.ConnectionError("refused")):
            res_en = test_agent.execute("what is my name?")
            res_hi = test_agent.execute("mera naam kya hai?")
        self.assertIn("offline", res_en.response.lower())
        self.assertIn("ollama", res_en.response.lower())
        self.assertIn("start nahi hai", res_hi.response.lower())

    def test_agent_falls_back_to_installed_ollama_model_on_404(self):
        test_agent = LocalAIAgent(ollama_url="http://localhost:11434", model_name="custom-llama")
        resp_404 = MagicMock()
        resp_404.status_code = 404

        resp_tags = MagicMock()
        resp_tags.status_code = 200
        resp_tags.json.return_value = {
            "models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.1-gpu:latest"}]
        }

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "message": {"role": "assistant", "content": "New Delhi is the capital of India."}
        }

        with patch.object(test_agent._session, "post", side_effect=[resp_404, resp_ok]), patch.object(
            test_agent._session, "get", return_value=resp_tags
        ):
            res = test_agent.execute("what is the capital of India?")

        self.assertEqual(test_agent.model_name, "llama3.1-gpu:latest")
        self.assertEqual(res.response, "New Delhi is the capital of India.")

    def test_select_fallback_language_rescues_low_confidence_exotic_lang(self):
        info = MagicMock()
        info.language = "ro"
        info.language_probability = 0.08
        info.all_language_probs = [("hi", 0.35), ("en", 0.20), ("ro", 0.08)]
        self.assertEqual(AnureetVoiceEngine._select_fallback_language(info), "hi")

        high_conf = MagicMock()
        high_conf.language = "fr"
        high_conf.language_probability = 0.91
        self.assertIsNone(AnureetVoiceEngine._select_fallback_language(high_conf))

    def test_whisper_transcribe_falls_back_to_cpu_on_cublas_error(self):
        engine = object.__new__(AnureetVoiceEngine)
        engine.whisper_device = "cuda"
        cuda_model = MagicMock()
        cuda_model.transcribe.side_effect = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

        cpu_model = MagicMock()
        cpu_seg = MagicMock()
        cpu_seg.text = "Anureet open chrome"
        cpu_model.transcribe.return_value = ([cpu_seg], None)
        engine.whisper_model = cuda_model

        with patch("voice_assistant.WhisperModel", return_value=cpu_model) as mock_cls:
            text = engine._transcribe_whisper(b"RIFF0000WAVE")

        self.assertEqual(text, "Anureet open chrome")
        self.assertEqual(engine.whisper_device, "cpu")
        mock_cls.assert_called_once()

    def test_search_files_skips_venv_and_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "venv").mkdir()
            Path(tmp, "venv", "target_match.py").write_text("x", encoding="utf-8")
            Path(tmp, "app_match.py").write_text("x", encoding="utf-8")
            res = file_tools.search_files("match.py", search_path=tmp)
            names = [m["name"] for m in res["matches"]]
            self.assertEqual(names, ["app_match.py"])

    def test_self_healing_python_exec_cleans_temp_file(self):
        before = set(os.listdir(Path.cwd()))
        res = deep_research_tools.self_healing_python_exec("print('ok-123')")
        after = set(os.listdir(Path.cwd()))
        self.assertIn("ok-123", res)
        self.assertEqual(before, after)

    def test_premiere_jsx_escapes_quotes(self):
        jsx_file = ROOT_DIR / "scripts" / "generated_premiere_task.jsx"
        original_content = jsx_file.read_text(encoding="utf-8") if jsx_file.exists() else None
        try:
            with patch("tools.premiere_tools.subprocess.Popen"):
                res = premiere_tools.premiere_import_and_setup(
                    r'C:\videos\my "clip".mp4',
                    add_white_matte=True,
                    cut_points=[1.5],
                )
            self.assertEqual(res["status"], "success")
            jsx_text = Path(res["script_path"]).read_text(encoding="utf-8")
            self.assertIn(r'\"clip\".mp4', jsx_text)
        finally:
            if original_content is not None:
                jsx_file.write_text(original_content, encoding="utf-8")

    def test_utility_weather_empty_daily_arrays_safe_and_cached(self):
        utility_tools._CACHE.clear()
        geo_payload = json.dumps({"results": [{"latitude": 29.97, "longitude": 76.88, "name": "Kurukshetra", "country": "India"}]}).encode()
        weather_payload = json.dumps({"current_weather": {"temperature": 25, "windspeed": 5, "weathercode": 0}, "daily": {"temperature_2m_max": [], "temperature_2m_min": []}}).encode()

        mock_geo = MagicMock()
        mock_geo.__enter__.return_value = mock_geo
        mock_geo.read.return_value = geo_payload

        mock_w = MagicMock()
        mock_w.__enter__.return_value = mock_w
        mock_w.read.return_value = weather_payload

        with patch("urllib.request.urlopen", side_effect=[mock_geo, mock_w]) as mock_open:
            first = utility_tools.get_weather("Kurukshetra")
            second = utility_tools.get_weather("Kurukshetra")
        self.assertEqual(first["status"], "success")
        self.assertIsNone(first["max_temperature_today"])
        self.assertEqual(second["summary"], first["summary"])
        self.assertEqual(mock_open.call_count, 2)  # Second call served from TTL cache

    def test_telegram_html_escape_and_chunking(self):
        formatted = telegram_bot.format_telegram_reply({
            "response_text": "2 < 3 & 5 > 1 <script>",
            "executed_tools": [{"tool": "x<y>", "details": "a & b", "execution_time_ms": 10}],
        })
        self.assertIn("&lt;script&gt;", formatted)
        self.assertIn("&amp;", formatted)
        chunks = telegram_bot.chunk_message("A" * 8500, limit=4000)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(c) <= 4000 for c in chunks))

    def test_memory_store_save_recall_forget_and_auto_extract(self):
        from tools import memory_store
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_mem.db"
            s1 = memory_store.save_memory("My favorite IDE is VS Code", db_path=db_path)
            self.assertEqual(s1["status"], "success")
            recalled = memory_store.recall_memory("VS Code", db_path=db_path)
            self.assertEqual(recalled["count"], 1)
            self.assertIn("VS Code", recalled["summary"])

            extracted = memory_store.auto_extract_memories("yaad rakhna mera naam Armaan hai", db_path=db_path)
            self.assertTrue(any("Armaan" in x for x in extracted))
            self.assertEqual(memory_store.auto_extract_memories("what is my name?", db_path=db_path), [])

            forgot = memory_store.forget_memory("VS Code", db_path=db_path)
            self.assertEqual(forgot["deleted_count"], 1)

    def test_productivity_clipboard_timers_todos_and_reminders(self):
        from tools import productivity_tools
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "prod_mem.db"
            with patch.object(productivity_tools, "pyperclip") as mock_clip:
                mock_clip.paste.return_value = "copied-text"
                c_res = productivity_tools.copy_to_clipboard("copied-text")
                g_res = productivity_tools.get_clipboard_content()
                self.assertEqual(c_res["status"], "success")
                self.assertEqual(g_res["content"], "copied-text")

            self.assertEqual(productivity_tools.parse_duration_seconds("5 min 30 sec"), 330)
            self.assertEqual(productivity_tools.parse_duration_seconds("2 ghante"), 7200)
            t_res = productivity_tools.set_timer("2 minutes", label="TestTimer")
            self.assertEqual(t_res["status"], "success")
            self.assertGreaterEqual(productivity_tools.list_timers()["count"], 1)
            c_timer = productivity_tools.cancel_timer("TestTimer")
            self.assertEqual(c_timer["status"], "success")

            productivity_tools.add_todo("Finish unit tests", db_path=db_path)
            self.assertEqual(productivity_tools.list_todos(db_path=db_path)["count"], 1)
            comp = productivity_tools.complete_todo("unit tests", db_path=db_path)
            self.assertEqual(comp["status"], "success")
            self.assertEqual(productivity_tools.list_todos(db_path=db_path)["count"], 0)

            productivity_tools.add_reminder("Drink water", "2020-01-01T10:00:00", db_path=db_path)
            due = productivity_tools.get_due_reminders(mark_triggered=True, db_path=db_path)
            self.assertEqual(due["count"], 1)
            self.assertEqual(productivity_tools.get_due_reminders(mark_triggered=False, db_path=db_path)["count"], 0)

    def test_fast_routes_productivity_and_briefing(self):
        test_agent = LocalAIAgent(ollama_url="http://localhost:11434", model_name="test-model")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fast_mem.db"
            with patch.object(config, "memory_db_path", db_path), patch(
                "tools.utility_tools.get_weather",
                return_value={"status": "success", "summary": "Weather in Kurukshetra: 25°C."},
            ):
                timer_res = test_agent.execute("2 minute ka timer lagao", session_id="t1")
                self.assertIn("set for 120 second", timer_res.response)
                test_agent.execute("cancel timer", session_id="t1")

                brief_res = test_agent.execute("morning briefing", session_id="t1")
                self.assertIn("Weather in Kurukshetra: 25°C.", brief_res.response)

    def test_pdf_read_file_and_widget_hotkey_parser(self):
        import anureet_widget
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            with open(pdf_path, "wb") as f:
                writer.write(f)
            pdf_res = file_tools.read_file(str(pdf_path))
            self.assertEqual(pdf_res["status"], "success")
            self.assertEqual(pdf_res["total_pages"], 1)

        self.assertEqual(anureet_widget.parse_hotkey_spec("ctrl+space"), (0x0002, 0x20))
        self.assertEqual(anureet_widget.parse_hotkey_spec("ctrl+alt+a"), (0x0003, ord("A")))

        engine = object.__new__(AnureetVoiceEngine)
        engine._stop_speech_event = __import__("threading").Event()
        engine.stop_speaking()
        self.assertTrue(engine._stop_speech_event.is_set())

    def test_widget_events_and_devanagari_translit(self):
        import voice_assistant
        from tools import memory_store

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "events.db"
            head0 = memory_store.get_widget_head_id(db_path=db_path)
            self.assertEqual(head0, 0)
            ev1 = memory_store.publish_widget_event("thinking", user_text="open notepad", source="voice", db_path=db_path)
            ev2 = memory_store.publish_widget_event(
                "speaking", user_text="open notepad", agent_text="Opening Notepad.", source="voice", db_path=db_path
            )
            self.assertGreater(ev2, ev1)
            events = memory_store.get_latest_widget_events(after_id=ev1, db_path=db_path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["state"], "speaking")
            self.assertEqual(events[0]["agent_text"], "Opening Notepad.")

        roman = voice_assistant._devanagari_to_hinglish("नमस्ते अनुरीत")
        self.assertNotIn("न", roman)
        self.assertIn("nam", roman.lower())

    def test_voice_speak_sapi_wav_and_widget_avatar_asset(self):
        import anureet_widget
        import voice_assistant

        self.assertTrue(Path(config.widget_avatar_path).exists())
        prepared = anureet_widget._prepare_sprite_image(Path(config.widget_avatar_path), (150, 150))
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.size, (150, 150))

        engine = AnureetVoiceEngine(init_stt=False)
        self.assertFalse(engine._stt_initialized)
        fake_voice = MagicMock()
        fake_voice.WaitUntilDone.return_value = True
        fake_voice.Status.RunningState = 1
        engine._tts_voice = fake_voice
        with patch.object(voice_assistant, "check_internet_connection", return_value=False), patch.object(
            engine, "_tts_queue"
        ) as mock_q:
            engine.speak("Test voice output ready.", blocking=False)
            mock_q.put.assert_called_once_with("Test voice output ready.")
            engine.stop_speaking()
            fake_voice.Speak.assert_called_with("", voice_assistant.SVSFPurgeBeforeSpeak)

        with patch("agent_core.open_application", return_value="Launched notepad") as mock_np:
            matched = LocalAIAgent(ollama_url="http://localhost:11434", model_name="test")._match_fast_route("अपन नोद पेर.")
            self.assertIsNotNone(matched)
            mock_np.assert_called_with("notepad")

    def test_mic_paused_while_command_runs(self):
        import voice_assistant

        engine = object.__new__(AnureetVoiceEngine)
        order = []

        def fake_handle(cmd):
            order.append(f"handle:{cmd}")
            return 123.0

        with patch.object(voice_assistant.time, "sleep"), patch.object(engine, "_handle_command", side_effect=fake_handle):
            res = engine._run_command_with_mic_paused(
                "open notepad",
                lambda: order.append("pause"),
                lambda: order.append("resume"),
            )
        self.assertEqual(res, 123.0)
        self.assertEqual(order, ["pause", "handle:open notepad", "resume"])

    def test_installed_app_discovery_and_multi_open(self):
        import tools.os_tools as os_tools

        fake_apps = [
            {"name": "Adobe After Effects 2026", "app_id": "Adobe.AfterEffects.2026"},
            {"name": "7-Zip File Manager", "app_id": "IgorPavlov.7zip"},
            {"name": "Notepad", "app_id": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
        ]
        with patch.object(os_tools, "_get_installed_apps_index", return_value=fake_apps), patch.object(
            os_tools.subprocess, "Popen"
        ) as mock_popen:
            listing = os_tools.list_installed_apps("adobe")
            self.assertEqual(listing["matched_count"], 1)
            self.assertIn("Adobe After Effects 2026", listing["apps"])

            msg = os_tools.open_application("after effects 2026 and 7-zip")
            self.assertIn("Opened", msg)
            cmds = [c.args[0] for c in mock_popen.call_args_list]
            self.assertEqual(
                cmds,
                [
                    ["explorer.exe", "shell:AppsFolder\\Adobe.AfterEffects.2026"],
                    ["explorer.exe", "shell:AppsFolder\\IgorPavlov.7zip"],
                ],
            )

    def test_hinglish_media_folder_and_style_routes(self):
        import agent_core
        import tools.os_tools as os_tools

        test_agent = LocalAIAgent(ollama_url="http://localhost:11434", model_name="test")
        self.assertEqual(agent_core._detect_query_style("haan bhai python samjhao"), "hinglish")

        with patch.object(os_tools.ctypes.windll.user32, "keybd_event") as mock_vk:
            next_res = test_agent._match_fast_route("agla gaana")
            self.assertEqual(next_res["name"], "media_control")
            self.assertIn("Agla gaana", next_res["output"])
            vol_res = test_agent._match_fast_route("awaaz badhao")
            self.assertEqual(vol_res["name"], "media_control")
            self.assertGreaterEqual(mock_vk.call_count, 2)

        with patch.object(os_tools.subprocess, "Popen"):
            folder_res = test_agent._match_fast_route("downloads kholo")
            self.assertEqual(folder_res["name"], "open_user_folder")
            self.assertIn("Downloads", folder_res["output"])

        with patch.object(os_tools, "_get_installed_apps_index", return_value=[]):
            self.assertIsNone(test_agent._match_fast_route("open netflix"))
            self.assertIsNone(test_agent._match_fast_route("open my_notes.txt"))

    def test_launcher_shutdown_and_ico_generation(self):
        import tempfile
        from pathlib import Path
        import install_desktop_shortcut
        import launch_anureet

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            runtime_path = tmp_path / "anureet_runtime.json"
            ico_out = tmp_path / "anureet_icon.ico"

            with patch.object(launch_anureet, "RUNTIME_FILE", runtime_path), patch.object(
                launch_anureet, "is_pid_running", return_value=True
            ), patch.object(launch_anureet, "terminate_pid", return_value=True) as mock_term:
                launch_anureet.save_runtime_state(
                    {
                        "widget_pid": os.getpid(),
                        "server_pid": 44444,
                        "ollama_pid": 55555,
                        "ollama_owned": False,
                    }
                )
                out = launch_anureet.shutdown_runtime(exclude_self=True)
                self.assertEqual(out["terminated_pids"], [44444])
                mock_term.assert_called_once_with(44444)
                self.assertFalse(runtime_path.exists())

            built = install_desktop_shortcut.build_ico_from_avatar(output_ico=ico_out)
            self.assertTrue(built.is_file())
            self.assertGreater(built.stat().st_size, 500)

    def test_ensure_server_running_finds_port_and_spawns_uvicorn(self):
        import launch_anureet

        fake_proc = MagicMock(pid=77777)
        fake_proc.poll.return_value = None
        with patch.object(launch_anureet, "is_gateway_online", side_effect=[False, True]), patch.object(
            launch_anureet, "_find_free_port", return_value=8005
        ) as mock_port, patch.object(launch_anureet.subprocess, "Popen", return_value=fake_proc) as mock_popen:
            pid, port = launch_anureet.ensure_server_running(preferred_port=8000, timeout=2.0)
            self.assertEqual(pid, 77777)
            self.assertEqual(port, 8005)
            mock_port.assert_called_once_with(8000)
            self.assertIn("uvicorn", " ".join(mock_popen.call_args.args[0]))


class TestLiveTranscriptFixes(unittest.TestCase):
    """Tests for self-shutdown, CapCut fuzzy match, window/tab switching, UI click, search bar typing, world time, and anti-hallucination."""

    def test_fuzzy_match_capgut_to_capcut_without_name_error(self):
        from tools import os_tools

        fake_apps = [{"name": "CapCut", "app_id": "c:.users.armaan.appdata.local.capcut.apps.9.5.0.4050.capcut.exe"}]
        with patch.object(os_tools, "_get_installed_apps_index", return_value=fake_apps):
            matched = os_tools._match_installed_app("capgut")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["name"], "CapCut")

    def test_close_application_and_protected_processes(self):
        from tools import os_tools

        with patch.object(
            os_tools,
            "_find_running_app_pids",
            return_value=[(9123, "CapCut.exe", "CapCut")],
        ), patch.object(
            os_tools.subprocess,
            "run",
            side_effect=[
                MagicMock(returncode=1, stdout="", stderr="not found"),
                MagicMock(returncode=0, stdout="SUCCESS", stderr=""),
            ],
        ) as mock_run:
            msg = os_tools.close_application("CapCut")
        self.assertIn("CapCut", msg)
        self.assertEqual(mock_run.call_count, 2)

        with patch.object(os_tools, "_find_running_app_pids", return_value=[]), \
             patch.object(os_tools.subprocess, "run") as mock_prot_run:
            prot_msg = os_tools.close_application("antigravity.exe")
        self.assertIn("Could not find", prot_msg)
        mock_prot_run.assert_not_called()

        with os_tools._SESSION_APPS_LOCK:
            os_tools._SESSION_OPENED_APPS.clear()
        os_tools._record_opened_app("CapCut")
        with patch.object(os_tools, "close_application", return_value="Closed capcut.") as mock_close:
            res = os_tools.close_session_apps()
        self.assertEqual(res["status"], "success")
        mock_close.assert_called_once_with("capcut")

    def test_focus_window_strips_punctuation_and_forces_activation(self):
        import pygetwindow as gw
        from tools import gui_vision_tools

        fake_wins = [
            MagicMock(title="Anureet Widget", _hWnd=101, isMinimized=False),
            MagicMock(title="YouTube - Google Chrome", _hWnd=202, isMinimized=False),
        ]
        with patch.object(gw, "getAllWindows", return_value=fake_wins), \
             patch.object(gui_vision_tools, "_activate_hwnd", return_value=True) as mock_act:
            res = gui_vision_tools.focus_window("chrome.")
        self.assertEqual(res["status"], "success")
        mock_act.assert_called_once_with(202)

    def test_browser_tab_action_and_type_in_search_bar(self):
        from tools import gui_vision_tools

        with patch.object(
            gui_vision_tools,
            "focus_window",
            side_effect=[
                {"status": "success", "title": "Google Chrome"},
                {"status": "success", "title": "Google Chrome"},
                {"status": "error", "message": "not found"},
                {"status": "success", "title": "Google Chrome"},
            ],
        ), \
             patch.object(gui_vision_tools, "_get_target_hwnd", return_value=(202, "YouTube - Google Chrome", "")), \
             patch.object(gui_vision_tools.pyautogui, "hotkey") as mock_hotkey, \
             patch.object(gui_vision_tools.pyautogui, "write") as mock_write, \
             patch.object(gui_vision_tools.pyautogui, "press") as mock_press, \
             patch.object(gui_vision_tools.time, "sleep"):
            r_next = gui_vision_tools.browser_tab_action("next")
            r_sw = gui_vision_tools.browser_tab_action("switch_to", "youtube")
            r_type = gui_vision_tools.type_in_search_bar("3JS", "youtube")

        self.assertIn("next browser tab", r_next)
        self.assertIn("youtube", r_sw.lower())
        self.assertIn("3JS", r_type)
        mock_hotkey.assert_any_call("ctrl", "tab")
        mock_hotkey.assert_any_call("ctrl", "shift", "a")
        mock_press.assert_any_call("/")
        mock_write.assert_any_call("youtube", interval=0.01)
        mock_write.assert_any_call("3JS", interval=0.01)
        mock_press.assert_any_call("enter")

    def test_click_screen_element_uia_and_ocr_fallback(self):
        from tools import gui_vision_tools

        with patch.object(gui_vision_tools, "_get_target_hwnd", return_value=(303, "CapCut", "")), \
             patch.object(gui_vision_tools, "_find_uia_element_center", return_value=(420, 310)), \
             patch.object(gui_vision_tools.pyautogui, "click") as mock_click:
            res_uia = gui_vision_tools.click_screen_element("create project", "capcut")
        self.assertEqual(res_uia["status"], "success")
        self.assertEqual(res_uia["method"], "uia")
        mock_click.assert_called_once_with(420, 310)

        with patch.object(gui_vision_tools, "_get_target_hwnd", return_value=(303, "CapCut", "")), \
             patch.object(gui_vision_tools, "_find_uia_element_center", return_value=None), \
             patch.object(gui_vision_tools, "_find_ocr_text_center", return_value=(510, 220)), \
             patch.object(gui_vision_tools.pyautogui, "click") as mock_click2:
            res_ocr = gui_vision_tools.click_screen_element("create project", "capcut")
        self.assertEqual(res_ocr["status"], "success")
        self.assertEqual(res_ocr["method"], "ocr")
        mock_click2.assert_called_once_with(510, 220)

        with patch.object(gui_vision_tools, "_get_target_hwnd", return_value=(303, "CapCut", "")), \
             patch.object(gui_vision_tools, "_find_uia_element_center", return_value=None), \
             patch.object(gui_vision_tools, "_find_ocr_text_center", return_value=None):
            res_miss = gui_vision_tools.click_screen_element("nonexistent button", "capcut")
        self.assertEqual(res_miss["status"], "error")

    def test_get_world_time_with_utc_offset(self):
        from tools import utility_tools

        with utility_tools._CACHE_LOCK:
            utility_tools._CACHE.clear()

        def _fake_resp(obj):
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock(read=lambda: json.dumps(obj).encode("utf-8"))
            cm.__exit__.return_value = False
            return cm

        geo_obj = {"results": [{"name": "Ottawa", "country": "Canada", "latitude": 45.42, "longitude": -75.69}]}
        meteo_obj = {"utc_offset_seconds": -18000, "timezone": "America/Toronto", "timezone_abbreviation": "EST"}

        with patch.object(utility_tools.urllib.request, "urlopen", side_effect=[_fake_resp(geo_obj), _fake_resp(meteo_obj)]):
            res = utility_tools.get_world_time("Canada")
        self.assertEqual(res["status"], "success")
        self.assertIn("Canada", res["summary"])
        self.assertIn("EST", res["summary"])
        self.assertNotIn("[Current Date and Time]", res["summary"])

    def test_transcript_fast_routes_and_no_notepad_hijack(self):
        import agent_core

        agent = agent_core.LocalAIAgent()
        with patch.object(agent_core, "get_weather", return_value={"status": "success", "city": "Delhi", "temperature_celsius": 28, "condition": "Clear", "wind_speed_kmh": 5, "max_temperature_today": 30, "min_temperature_today": 19, "summary": "Delhi 28C"}) as m_w, \
             patch.object(agent_core, "get_world_time", return_value={"status": "success", "location": "Canada (Ottawa)", "time": "08:30 AM", "date": "Thursday, 2026-11-12", "timezone_abbr": "EST", "summary": "Current time in Canada (Ottawa) is 08:30 AM"}) as m_t, \
             patch.object(agent_core, "type_in_search_bar", return_value="Typed '3JS' into search bar in 'YouTube - Google Chrome'.") as m_sb, \
             patch.object(agent_core, "write_to_notepad") as m_np, \
             patch.object(agent_core, "click_screen_element", return_value={"status": "success", "message": "Clicked 'create project'."}) as m_clk, \
             patch.object(agent_core, "browser_tab_action", side_effect=["Switched to the next browser tab.", "Switched browser tab matching 'Youtube'."]) as m_tab, \
             patch.object(agent_core, "focus_window", side_effect=[{"status": "success", "message": "Switched focus to window: Google Chrome"}, {"status": "error", "message": "No open window"}]) as m_foc, \
             patch.object(agent_core, "shutdown_anureet", return_value={"status": "success", "message": "Shutting down."}) as m_shut:
            self.assertEqual(agent.execute("Current weather of Delhi.").tool_calls_made[0]["tool"], "get_weather")
            m_w.assert_called_once_with("Delhi")

            self.assertEqual(agent.execute("What is the current time in Canada?").tool_calls_made[0]["tool"], "get_world_time")
            wt_hi = agent.execute("Canada mein kya time hai?")
            self.assertEqual(wt_hi.tool_calls_made[0]["tool"], "get_world_time")
            self.assertIn("08:30 AM", wt_hi.response)
            m_t.assert_any_call("Canada")

            self.assertEqual(agent.execute("Type 3JS in search bar.").tool_calls_made[0]["tool"], "type_in_search_bar")
            m_sb.assert_called_once_with("3JS", app_hint=None)
            m_np.assert_not_called()

            self.assertEqual(agent.execute("Click on create project in capcut.").tool_calls_made[0]["tool"], "click_screen_element")
            m_clk.assert_called_once_with("create project", app_name="CapCut")

            self.assertEqual(agent.execute("Switch tab.").tool_calls_made[0]["tool"], "browser_tab_action")
            m_tab.assert_any_call("next")

            self.assertEqual(agent.execute("Switch to chrome.").tool_calls_made[0]["tool"], "focus_window")
            self.assertEqual(agent.execute("Youtube window pr switch karo.").tool_calls_made[0]["tool"], "focus_window")
            self.assertEqual(m_foc.call_count, 2)
            self.assertTrue(any("youtube" in str(call).lower() for call in m_tab.call_args_list))

            self.assertEqual(agent.execute("Stop and close everything.").tool_calls_made[0]["tool"], "shutdown_anureet")
            self.assertEqual(agent.execute("Switch off yourself.").tool_calls_made[0]["tool"], "shutdown_anureet")
            self.assertEqual(agent.execute("Do yourself down.").tool_calls_made[0]["tool"], "shutdown_anureet")
            m_shut.assert_any_call(close_launched_apps=True)
            m_shut.assert_any_call(close_launched_apps=False)

    def test_ollama_failed_tool_framing_and_low_temperature(self):
        import agent_core

        agent = agent_core.LocalAIAgent()
        captured_payloads = []
        turn_responses = [
            MagicMock(
                status_code=200,
                json=lambda: {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": "focus_window", "arguments": {"title_query": "MissingApp"}}}],
                    }
                },
            ),
            MagicMock(
                status_code=200,
                json=lambda: {
                    "message": {
                        "role": "assistant",
                        "content": "Sorry, MissingApp window nahi mili.",
                    }
                },
            ),
        ]

        def _fake_post(url, json=None, timeout=None):
            import copy
            captured_payloads.append(copy.deepcopy(json))
            return turn_responses[len(captured_payloads) - 1]

        for r in turn_responses:
            r.raise_for_status = lambda: None

        with patch.object(agent._session, "post", side_effect=_fake_post), \
             patch.dict(agent.tools, {"focus_window": agent_core.ToolDeclaration(name="focus_window", func=lambda title_query="": {"status": "not_found", "message": "No open window matching 'MissingApp' found."}, description="focus", parameters={"type": "object", "properties": {}})}):
            res = agent.execute("Please find my nonexistent special app window")

        self.assertIn("MissingApp", res.response)
        self.assertEqual(captured_payloads[0]["options"]["temperature"], 0.1)
        self.assertEqual(captured_payloads[0]["options"]["num_ctx"], 2048)
        self.assertEqual(captured_payloads[0]["options"]["num_batch"], 256)
        self.assertEqual(captured_payloads[0]["options"]["num_thread"], 6)
        tool_msgs = [m for m in captured_payloads[1]["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertTrue(tool_msgs[0]["content"].startswith("[TOOL_FAILED - REPORT THIS HONESTLY, DO NOT CLAIM IT SUCCEEDED]:"))

    def test_widget_arbitrary_resize_and_persistence(self):
        import anureet_widget

        self.assertEqual(anureet_widget.clamp_widget_scale(0.1, 1920, 1080), anureet_widget.MIN_WIDGET_SCALE)
        self.assertEqual(anureet_widget.clamp_widget_scale(1.65, 1920, 1080), 1.65)
        self.assertLessEqual(anureet_widget.clamp_widget_scale(99.0, 1920, 1080), anureet_widget.MAX_WIDGET_SCALE)

        with tempfile.TemporaryDirectory() as tmp:
            prefs_file = Path(tmp) / "widget_prefs.json"
            snapshots = []

            def _exercise(app):
                app.set_scale(0.55)
                snapshots.append(("shrunk", app.scale, app.stage_w, app.stage_h))
                app.set_scale(2.25)
                snapshots.append(("enlarged", app.scale, app.stage_w, app.stage_h))
                app.resize_hud(520, 610)
                snapshots.append(("hud", app.hud_w, app.hud_h))
                app._on_orb_wheel(MagicMock(delta=120))
                snapshots.append(("wheel_up", app.scale))
                app._on_orb_wheel(MagicMock(delta=-120))
                snapshots.append(("wheel_down", app.scale))
                app.root.after(20, app.close)

            w1 = anureet_widget.AnureetWidgetApp(on_ready=_exercise, prefs_path=prefs_file, prewarm_voice=False)
            w1.run()

            self.assertEqual(snapshots[0][1], 0.55)
            self.assertLess(snapshots[0][2], anureet_widget.BASE_STAGE_W)
            self.assertEqual(snapshots[1][1], 2.25)
            self.assertGreater(snapshots[1][2], anureet_widget.BASE_STAGE_W * 2)
            self.assertEqual(snapshots[2], ("hud", 520, 610))
            self.assertGreater(snapshots[3][1], 2.25)
            self.assertTrue(prefs_file.exists())

            restored = []
            w2 = anureet_widget.AnureetWidgetApp(
                on_ready=lambda app: (restored.append((app.scale, app.hud_w, app.hud_h)), app.root.after(20, app.close)),
                prefs_path=prefs_file,
                prewarm_voice=False,
            )
            w2.run()
            self.assertAlmostEqual(restored[0][0], snapshots[4][1], places=2)
            self.assertEqual(restored[0][1:], (520, 610))

    def test_widget_minimize_restore_and_character_blink_animation(self):
        import anureet_widget

        with tempfile.TemporaryDirectory() as tmp:
            prefs_file = Path(tmp) / "widget_prefs.json"
            observations = {}

            def _exercise(app):
                try:
                    observations["has_blink"] = "blink" in app._sprite_frames
                    observations["init_minimized"] = app.is_minimized

                    # Test minimize badge hit detection
                    mx, my = app._min_badge_pos
                    observations["hit_min_badge"] = app._hit_min_badge(mx, my)
                    observations["hit_elsewhere"] = app._hit_min_badge(0, 0)

                    # Test minimizing widget
                    orig_w, orig_h = app.stage_w, app.stage_h
                    app.minimize_widget()
                    observations["is_minimized"] = app.is_minimized
                    observations["min_dims"] = (app.stage_w, app.stage_h)

                    # Test restoring widget
                    app.restore_widget()
                    observations["is_restored"] = not app.is_minimized
                    observations["restored_dims"] = (app.stage_w, app.stage_h)

                    # Test toggle minimize
                    app.toggle_minimize()
                    observations["toggled_min"] = app.is_minimized
                    app.toggle_minimize()
                    observations["toggled_restored"] = not app.is_minimized

                    # Test unminimize on scaling
                    app.minimize_widget()
                    app.adjust_scale(1.1)
                    observations["unmin_on_scale"] = not app.is_minimized
                finally:
                    app.root.after(10, app.close)

            w = anureet_widget.AnureetWidgetApp(on_ready=_exercise, prefs_path=prefs_file, prewarm_voice=False)
            w.run()

            self.assertTrue(observations["has_blink"])
            self.assertFalse(observations["init_minimized"])
            self.assertTrue(observations["hit_min_badge"])
            self.assertFalse(observations["hit_elsewhere"])
            self.assertTrue(observations["is_minimized"])
            self.assertEqual(observations["min_dims"], (142, 38))
            self.assertTrue(observations["is_restored"])
            self.assertEqual(observations["restored_dims"], (anureet_widget.BASE_STAGE_W, anureet_widget.BASE_STAGE_H))
            self.assertTrue(observations["toggled_min"])
            self.assertTrue(observations["toggled_restored"])
            self.assertTrue(observations["unmin_on_scale"])


class TestAirGappedArchitecture(unittest.TestCase):
    """End-to-end verification of the 4-phase offline refactoring."""

    def test_phase1_offline_env_vars_and_cpu_whisper_pinning(self):
        import voice_assistant

        for k, expected in (
            ("HF_HUB_OFFLINE", "1"),
            ("TRANSFORMERS_OFFLINE", "1"),
            ("OLLAMA_HOST", "127.0.0.1:11434"),
            ("OLLAMA_FLASH_ATTENTION", "1"),
            ("OLLAMA_KV_CACHE_TYPE", "q8_0"),
            ("OLLAMA_MAX_LOADED_MODELS", "1"),
            ("OLLAMA_NUM_PARALLEL", "1"),
            ("OLLAMA_NOPRUNE", "1"),
        ):
            self.assertEqual(os.environ.get(k), expected)

        self.assertFalse(voice_assistant.check_internet_connection())
        engine = object.__new__(AnureetVoiceEngine)
        mock_candidate = MagicMock()
        with patch("voice_assistant.WhisperModel", return_value=mock_candidate) as mock_cls:
            engine._init_whisper_model()
        mock_cls.assert_called_once_with(
            config.whisper_model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
            local_files_only=True,
        )
        self.assertEqual(engine.whisper_device, "cpu")
        self.assertEqual(engine.whisper_compute_type, "int8")

    def test_phase2_loopback_dual_model_routing_and_pydantic_format(self):
        import agent_core

        a = LocalAIAgent()
        self.assertFalse(a._session.trust_env)
        self.assertEqual(a._select_model_for_query("how is kurukshetra today?"), "llama3.1-gpu:latest")
        self.assertEqual(a._select_model_for_query("debug python traceback in script"), "qwen2.5-coder:7b")

        captured = {}

        def _fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            resp.json.return_value = {
                "message": {
                    "role": "assistant",
                    "content": '{"action":"final_answer","tool_name":null,"arguments":{},"response":"Offline ok"}',
                }
            }
            return resp

        with patch.object(a._session, "post", side_effect=_fake_post):
            action = a.plan_structured_action("write a python function to sort numbers")
        self.assertEqual(captured["json"]["model"], "qwen2.5-coder:7b")
        self.assertEqual(captured["json"]["format"], agent_core.AgentAction.model_json_schema())
        self.assertEqual(action.action, "final_answer")
        self.assertEqual(action.response, "Offline ok")

    def test_phase3_streaming_clauses_and_sapi5_barge_in(self):
        import agent_core

        spoken = []
        buf = agent_core._ClauseBuffer(spoken.append, min_chars=25)
        buf.feed("Haan bilkul friend, main aapki ")
        self.assertEqual(spoken, [])
        buf.feed("madad abhi karta hoon. Aur next line!")
        self.assertEqual(spoken, ["Haan bilkul friend, main aapki madad abhi karta hoon."])
        buf.flush()
        self.assertEqual(spoken[-1], "Aur next line!")

        agent = LocalAIAgent(ollama_url="http://127.0.0.1:11434", model_name="test-model")
        stream_lines = [
            json.dumps({"message": {"role": "assistant", "content": "Pehla sentence ready hai yahan. "}, "done": False}),
            json.dumps({"message": {"role": "assistant", "content": "Dusra part bhi aa gaya!"}, "done": True}),
        ]
        stream_resp = MagicMock(status_code=200)
        stream_resp.raise_for_status = lambda: None
        stream_resp.iter_lines.return_value = iter(stream_lines)

        out_clauses = []
        with patch.object(agent._session, "post", return_value=stream_resp):
            res = agent.stream_query("tell me a short story offline", on_clause=out_clauses.append, session_id="stream_t")
        self.assertEqual(len(out_clauses), 2)
        self.assertIn("Pehla sentence", res["response_text"])

    def test_phase4_fts5_bm25_sandbox_and_zero_external_sockets(self):
        import socket
        from tools import memory_store

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bm25.db"
            memory_store.save_memory("I love fast Python scripting and local agents", db_path=db_path)
            memory_store.save_memory("Python python python optimization notes for offline AI", db_path=db_path)
            memory_store.save_memory("Favorite snack is samosa", db_path=db_path)
            recalled = memory_store.recall_memory("python", limit=5, db_path=db_path)
            self.assertEqual(recalled["count"], 2)
            with memory_store._connect(db_path) as conn:
                mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                sync_val = conn.execute("PRAGMA synchronous;").fetchone()[0]
                self.assertEqual(str(mode).lower(), "wal")
                self.assertIn(int(sync_val), (1, 2))  # NORMAL=1

        # Verify zero external sockets outside 127.0.0.1
        external_targets = []
        orig_connect = socket.socket.connect

        def _guard_connect(sock_self, address):
            host = address[0] if isinstance(address, tuple) and address else ""
            if host not in {"127.0.0.1", "localhost", "::1", ""}:
                external_targets.append(host)
                raise OSError(f"Blocked external socket in offline test: {host}")
            return orig_connect(sock_self, address)

        with patch.object(socket.socket, "connect", _guard_connect):
            brief = deep_research_tools.deep_research_topic("Python")
            self.assertIn("Offline Intelligence Briefing", brief)
            py_res = deep_research_tools.run_sandboxed_code("print('sandbox-ok')", language="python", timeout=10)
            self.assertIn("sandbox-ok", py_res)
            timeout_res = deep_research_tools.run_sandboxed_code(
                "import time; time.sleep(20)", language="python", timeout=1
            )
            self.assertIn("timed out", timeout_res.lower())
        self.assertEqual(external_targets, [])

    def test_indian_female_voice_priority_and_soft_ssml(self):
        import voice_assistant

        def _mk_tok(desc, gender, lang):
            tok = MagicMock()
            tok.GetDescription.return_value = desc
            tok.GetAttribute.side_effect = lambda key: {"Gender": gender, "Language": lang}.get(key, "")
            return tok

        tok_david = _mk_tok("Microsoft David Desktop - English (United States)", "Male", "409")
        tok_ravi = _mk_tok("Microsoft Ravi - English (India)", "Male", "4009")
        tok_zira = _mk_tok("Microsoft Zira Desktop - English (United States)", "Female", "409")
        tok_heera = _mk_tok("Microsoft Heera - English (India)", "Female", "4009")
        fake_voice = MagicMock()

        with patch.object(
            AnureetVoiceEngine,
            "_collect_sapi5_tokens",
            return_value=[tok_david, tok_ravi, tok_zira, tok_heera],
        ):
            selected = AnureetVoiceEngine._select_sapi5_female_voice(fake_voice)

        self.assertEqual(fake_voice.Voice, tok_heera)
        self.assertIn("heera", selected)
        ssml = AnureetVoiceEngine._format_soft_indian_ssml("A & B <ready>")
        self.assertIn('<pitch absmiddle="+1">', ssml)
        self.assertIn("A &amp; B &lt;ready&gt;", ssml)


class TestWidgetSyncAndRobustness(unittest.TestCase):
    """Regression tests for widget sync, female voice endpoints, tool resilience, and fast routes."""

    @classmethod
    def setUpClass(cls):
        from server import app
        from fastapi.testclient import TestClient
        cls.client = TestClient(app)

    def test_create_folder_fast_routes_and_execution(self):
        import agent_core
        from tools.file_tools import create_folder

        with tempfile.TemporaryDirectory() as tmp_dir:
            res = create_folder("SubTestFolder", parent_dir=tmp_dir)
            self.assertEqual(res["status"], "success")
            self.assertTrue((Path(tmp_dir) / "SubTestFolder").is_dir())

        with patch("agent_core.create_folder", return_value={"status": "success", "message": "Created folder 'AI'."}) as mock_cf:
            res1 = agent_core.agent.execute("Create a new folder on desktop named AI")
            self.assertTrue(res1.routed_fast)
            self.assertIn("Created folder", res1.response)
            mock_cf.assert_called_with("AI", parent_dir="desktop")

            res2 = agent_core.agent.execute("desktop pe index.ai folder banao")
            self.assertTrue(res2.routed_fast)
            mock_cf.assert_called_with("index.ai", parent_dir="desktop")

    def test_minimize_all_windows_fast_route(self):
        import agent_core

        with patch("agent_core.minimize_all_windows", return_value={"status": "success", "message": "Minimized all open windows."}) as mock_min, \
             patch("agent_core.close_session_apps") as mock_close:
            res = agent_core.agent.execute("Minimize all app")
            self.assertTrue(res.routed_fast)
            self.assertIn("Minimized all open windows", res.response)
            mock_min.assert_called_once()
            mock_close.assert_not_called()

    def test_browser_tab_switch_again_and_missing_args(self):
        import agent_core
        from tools.gui_vision_tools import browser_tab_action

        with patch("agent_core.browser_tab_action", return_value="Switched to next tab.") as mock_bta:
            res = agent_core.agent.execute("Switch again")
            self.assertTrue(res.routed_fast)
            mock_bta.assert_called_with("next")

        # Calling directly with missing positional argument must not raise TypeError
        with patch("pyautogui.hotkey"):
            safe_call_res = browser_tab_action()
            self.assertIsInstance(safe_call_res, str)

    def test_click_screen_element_missing_args_and_fuzzy_focus(self):
        from tools.gui_vision_tools import click_screen_element, focus_window

        # Empty target_text must return error dict, never crash with TypeError
        res_empty = click_screen_element()
        self.assertEqual(res_empty.get("status"), "error")

        # Fuzzy window match: "capquette" resolves to "CapCut"
        mock_win = MagicMock()
        mock_win.title = "CapCut - Video Editor"
        with patch("pygetwindow.getAllWindows", return_value=[mock_win]), \
             patch("pygetwindow.getWindowsWithTitle", return_value=[]), \
             patch("tools.os_tools._match_installed_app", return_value="CapCut"):
            res_focus = focus_window("capquette")
            self.assertEqual(res_focus["status"], "success")
            self.assertIn("CapCut", res_focus["message"])

    def test_malformed_parameters_json_tool_repair(self):
        import agent_core

        agent = agent_core.LocalAIAgent()
        # Missing colon: "parameters{"
        malformed = '{"type":"function","name":"click_screen_element","parameters{"target_text":"New folder"}}'
        parsed = agent._parse_embedded_json_tool_call(malformed)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "click_screen_element")
        self.assertEqual(parsed["args"].get("target_text"), "New folder")

    def test_compact_tool_selection_under_num_ctx(self):
        import agent_core

        agent = agent_core.LocalAIAgent()
        schemas = agent._select_tool_schemas_for_query("Can you switch tab in Chrome and open youtube?")
        self.assertLessEqual(len(schemas), 14)
        names = {s["function"]["name"] for s in schemas}
        self.assertIn("browser_tab_action", names)
        self.assertIn("open_application", names)

    def test_server_synced_history_events_and_tts_endpoints(self):
        # 1. /api/history returns valid list
        r_hist = self.client.get("/api/history?limit=10")
        self.assertEqual(r_hist.status_code, 200)
        self.assertEqual(r_hist.json()["status"], "success")
        self.assertIsInstance(r_hist.json()["history"], list)

        # 2. /api/events returns valid list and head_id
        r_ev = self.client.get("/api/events?after_id=0")
        self.assertEqual(r_ev.status_code, 200)
        self.assertEqual(r_ev.json()["status"], "success")
        self.assertIn("head_id", r_ev.json())

        # 3. /api/tts returns audio/wav
        with patch.object(AnureetVoiceEngine, "synthesize_offline_female_wav", return_value=b"RIFF\x24\x00\x00\x00WAVEfmt "):
            r_tts = self.client.get("/api/tts?text=Hello+Anureet")
            self.assertEqual(r_tts.status_code, 200)
            self.assertEqual(r_tts.headers["content-type"], "audio/wav")
            self.assertTrue(r_tts.content.startswith(b"RIFF"))

    def test_download_file_tool(self):
        from tools.file_tools import download_file
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_bytes = b"PDF-1.4 Mock Problem Statement Content"
            mock_resp = io.BytesIO(dummy_bytes)
            with patch("urllib.request.urlopen", return_value=mock_resp):
                res = download_file(
                    url="https://unstop.com/voyaghack/problem_statement.pdf",
                    destination_path=tmpdir,
                    filename="voyaghack_ps.pdf",
                )
                self.assertEqual(res["status"], "success")
                self.assertEqual(res["size_bytes"], len(dummy_bytes))
                saved_path = Path(res["file_path"])
                self.assertTrue(saved_path.is_file())
                self.assertEqual(saved_path.read_bytes(), dummy_bytes)

    def test_open_website_tool(self):
        from tools.os_tools import open_website
        with patch("webbrowser.open") as mock_wb:
            res = open_website("unstop.com/hackathons/voyaghack")
            mock_wb.assert_called_once_with("https://unstop.com/hackathons/voyaghack")
            self.assertIn("Opened https://unstop.com/hackathons/voyaghack", res)

    def test_plan_and_execute_tool(self):
        import agent_core
        from unittest.mock import MagicMock

        agent = agent_core.LocalAIAgent()
        plan_json = json.dumps([
            {"step": 1, "tool": "create_folder", "args": {"folder_name": "hack", "parent_dir": "desktop"}, "description": "Create folder"},
            {"step": 2, "tool": "open_website", "args": {"url": "https://unstop.com/hackathons/voyaghack"}, "description": "Open Unstop"}
        ])

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": f"```json\n{plan_json}\n```"}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.Session.post", return_value=mock_resp), \
             patch.object(agent.tools["create_folder"], "func", return_value={"status": "success", "message": "Created folder 'hack'", "folder_path": "C:\\Desktop\\hack"}), \
             patch.object(agent.tools["open_website"], "func", return_value="Opened https://unstop.com in Chrome."):
            summary = agent.plan_and_execute("Create hack folder and open unstop")
            self.assertIn("Task execution complete:", summary)
            self.assertIn("Step 1 (Create folder): Created folder 'hack'", summary)
            self.assertIn("Step 2 (Open Unstop): Opened https://unstop.com in Chrome.", summary)

    def test_phonetic_normalization_and_alias_resolution(self):
        from tools.query_normalizer import normalize_voice_query, resolve_app_name, NON_APP_WORDS

        # 1. Phonetic app resolutions
        self.assertEqual(resolve_app_name("capgut"), "CapCut")
        self.assertEqual(resolve_app_name("capquette"), "CapCut")
        self.assertEqual(resolve_app_name("jutabh"), "YouTube")
        self.assertEqual(resolve_app_name("crome"), "Google Chrome")
        self.assertEqual(resolve_app_name("sportify"), "Spotify")

        # 2. Non-app word guards
        for word in ("audio", "desktop", "folder", "file", "tab"):
            self.assertIn(word, NON_APP_WORDS)
            self.assertEqual(resolve_app_name(word), word)

        # 3. Action phrase normalization
        self.assertEqual(
            normalize_voice_query("Click on audio in capquette."),
            "Click on audio in CapCut."
        )
        self.assertEqual(
            normalize_voice_query("Ilika ona audio in capgut."),
            "click on audio in CapCut."
        )
        self.assertEqual(
            normalize_voice_query("Close, grab, cut."),
            "close CapCut."
        )
        self.assertEqual(
            normalize_voice_query("Switch again."),
            "switch tab"
        )
        self.assertEqual(
            normalize_voice_query("Saare app minimize."),
            "minimize all windows."
        )

    def test_live_desktop_context_injection_in_ollama_prompt(self):
        import agent_core
        from unittest.mock import MagicMock, patch

        agent = agent_core.LocalAIAgent()
        ctx = agent._get_live_desktop_context()
        self.assertIn("active_window", ctx)
        self.assertIn("open_windows", ctx)
        self.assertIn("installed_apps", ctx)
        self.assertIsInstance(ctx["open_windows"], list)
        self.assertIsInstance(ctx["installed_apps"], list)

        captured_messages = []
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"role": "assistant", "content": "Done."}}
        mock_resp.raise_for_status = MagicMock()

        def _fake_post(url, json=None, timeout=None):
            if json and "messages" in json:
                captured_messages.extend(json["messages"])
            return mock_resp

        with patch.object(agent._session, "post", side_effect=_fake_post):
            agent.execute("Explain quantum entanglement in simple terms")

        system_msg = next((m for m in captured_messages if m.get("role") == "system"), None)
        self.assertIsNotNone(system_msg)
        self.assertIn("[Live Desktop Environment]:", system_msg["content"])
        self.assertIn("[Speech Recognition Phonetic Correction Directive]:", system_msg["content"])
        self.assertIn("Active Foreground Window:", system_msg["content"])

    def test_tool_arguments_hardening_and_fallbacks(self):
        import agent_core
        from unittest.mock import MagicMock, patch

        agent = agent_core.LocalAIAgent()

        # browser_tab_action defaults action="next" when called with empty args
        with patch.dict(agent.tools, {"browser_tab_action": agent_core.ToolDeclaration(
            name="browser_tab_action",
            func=lambda action="next", **kw: f"Tab action: {action}",
            description="tab action",
            parameters={"type": "object", "properties": {}}
        )}):
            res = agent._invoke_tool("browser_tab_action", {})
            self.assertTrue(res["ok"])
            self.assertEqual(res["arguments"].get("action"), "next")

        # click_screen_element maps alias "element" to "target_text"
        with patch.dict(agent.tools, {"click_screen_element": agent_core.ToolDeclaration(
            name="click_screen_element",
            func=lambda target_text="", **kw: f"Clicked {target_text}",
            description="click",
            parameters={"type": "object", "properties": {}}
        )}):
            res = agent._invoke_tool("click_screen_element", {"element": "audio"})
            self.assertTrue(res["ok"])
            self.assertEqual(res["arguments"].get("target_text"), "audio")

        # close_application maps alias "app" to "app_name"
        with patch.dict(agent.tools, {"close_application": agent_core.ToolDeclaration(
            name="close_application",
            func=lambda app_name="", **kw: f"Closed {app_name}",
            description="close",
            parameters={"type": "object", "properties": {}}
        )}):
            res = agent._invoke_tool("close_application", {"app": "capcut"})
            self.assertTrue(res["ok"])
            self.assertEqual(res["arguments"].get("app_name"), "capcut")

    def test_malformed_raw_json_tool_interception(self):
        import agent_core

        agent = agent_core.LocalAIAgent()

        # 1. Malformed 'parameters{' without colon
        malformed_1 = '{"type":"function","name":"click_screen_element","parameters{"target_text":"New folder"}}'
        parsed_1 = agent._parse_embedded_json_tool_call(malformed_1)
        self.assertIsNotNone(parsed_1)
        self.assertEqual(parsed_1["name"], "click_screen_element")
        self.assertEqual(parsed_1["args"], {"target_text": "New folder"})

        # 2. Malformed complete_todo tool call
        malformed_2 = '{"type":"function","name":"complete_todo","parameters{"task_query":"Good year, no"}}'
        parsed_2 = agent._parse_embedded_json_tool_call(malformed_2)
        self.assertIsNotNone(parsed_2)
        self.assertEqual(parsed_2["name"], "complete_todo")
        self.assertEqual(parsed_2["args"], {"task_query": "Good year, no"})

    def test_widget_hud_chat_bar_toggle_and_persistence(self):
        import anureet_widget
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            prefs_file = Path(tmp) / "widget_prefs.json"
            events = []

            def _exercise(app):
                try:
                    # 1. Initial state: chat bar should be visible by default
                    events.append(("init_visible", app.hud_chat_bar_visible))
                    events.append(("init_btn_fg", app.chat_bar_btn.cget("fg")))
                    events.append(("init_pack_manager", app.hud_chat_bar.winfo_manager()))

                    # 2. Toggle to hidden
                    app.toggle_hud_chat_bar()
                    events.append(("toggled_visible", app.hud_chat_bar_visible))
                    events.append(("toggled_btn_fg", app.chat_bar_btn.cget("fg")))
                    events.append(("toggled_pack_manager", app.hud_chat_bar.winfo_manager()))
                    events.append(("toggled_var", app._chat_bar_var.get()))

                    # 3. Toggle back to visible
                    app.toggle_hud_chat_bar(visible=True)
                    events.append(("restored_visible", app.hud_chat_bar_visible))
                    events.append(("restored_pack_manager", app.hud_chat_bar.winfo_manager()))

                    # 4. Set to hidden and persist
                    app.set_hud_chat_bar_visible(False)
                    app._flush_prefs()
                finally:
                    app.root.after(10, app.close)

            w1 = anureet_widget.AnureetWidgetApp(on_ready=_exercise, prefs_path=prefs_file, prewarm_voice=False)
            w1.run()

            self.assertEqual(events[0], ("init_visible", True))
            self.assertEqual(events[1], ("init_btn_fg", "#38bdf8"))
            self.assertEqual(events[2], ("init_pack_manager", "pack"))

            self.assertEqual(events[3], ("toggled_visible", False))
            self.assertEqual(events[4], ("toggled_btn_fg", "#64748b"))
            self.assertEqual(events[5], ("toggled_pack_manager", ""))
            self.assertEqual(events[6], ("toggled_var", False))

            self.assertEqual(events[7], ("restored_visible", True))
            self.assertEqual(events[8], ("restored_pack_manager", "pack"))

            # 5. Verify persistence across widget restarts
            restored = []
            def _check_restored(app):
                try:
                    restored.append(("visible", app.hud_chat_bar_visible))
                    restored.append(("btn_fg", app.chat_bar_btn.cget("fg")))
                    restored.append(("pack_manager", app.hud_chat_bar.winfo_manager()))
                finally:
                    app.root.after(10, app.close)

            w2 = anureet_widget.AnureetWidgetApp(on_ready=_check_restored, prefs_path=prefs_file, prewarm_voice=False)
            w2.run()

            self.assertEqual(restored[0], ("visible", False))
            self.assertEqual(restored[1], ("btn_fg", "#64748b"))
            self.assertEqual(restored[2], ("pack_manager", ""))

    def test_hud_chat_bar_fast_routes(self):
        import agent_core

        agent = agent_core.LocalAIAgent()

        res_hide = agent.process_query("hide hud chat bar")
        self.assertIn("hidden", res_hide.get("response_text", "").lower())

        res_show = agent.process_query("show the hud chat bar")
        self.assertIn("visible", res_show.get("response_text", "").lower())

        res_hinglish = agent.process_query("chat bar hide karo")
        self.assertIn("hidden", res_hinglish.get("response_text", "").lower())

    def test_widget_continuous_mode_toggle(self):
        import anureet_widget
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            prefs_file = Path(tmp) / "widget_prefs.json"
            obs = {}

            def _exercise(app):
                try:
                    obs["init_cont"] = app.is_continuous_mode_active()
                    app.start_continuous_mode(60.0, speak_announcement=False)
                    obs["started_cont"] = app.is_continuous_mode_active()
                    obs["started_btn_text"] = app.continuous_btn.cget("text")
                    app.stop_continuous_mode(speak_announcement=False)
                    obs["stopped_cont"] = app.is_continuous_mode_active()
                    obs["stopped_btn_text"] = app.continuous_btn.cget("text")
                finally:
                    app.root.after(10, app.close)

            w = anureet_widget.AnureetWidgetApp(on_ready=_exercise, prefs_path=prefs_file, prewarm_voice=False)
            w.run()

            self.assertFalse(obs["init_cont"])
            self.assertTrue(obs["started_cont"])
            self.assertTrue(obs["started_btn_text"].startswith("♾️"))
            self.assertFalse(obs["stopped_cont"])
            self.assertEqual(obs["stopped_btn_text"], "♾️")


if __name__ == "__main__":
    unittest.main()
