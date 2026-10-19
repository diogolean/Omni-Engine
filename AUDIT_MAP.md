# Auditoria do Omni-Engine (main 121e046, só leitura)

Método: grafo de imports via `ast` (resolve imports relativos), funções de topo com o mesmo nome em pacotes diferentes, `rg` para caminhos e referências em docs, compose, workflows e scripts. Limitação: imports dinâmicos (`importlib`, `python -m`) só aparecem pelo `rg`, então "ninguém importa" ≠ morto.

## 1. Mapa de pastas
- **engine/** (26 py): orquestração do reel (`orchestrator`, `pipeline/stage_*`, `cli*`, `voice.py`, `log.py`, `reporting.py`). `main.py` delega para cá.
- **core/** (88): motores de conteúdo (`economic_reel_lofi`, `principles_of_wealth`, `animator`, wan/reference/sequence engines), `reel_ffmpeg_render`, custo (`cost_tracker`, `spend_ledger`, `google_guardrail`), `cheap_llm`, `remote_gpu_manager` (ComfyUI/RunPod) e `interfaces` (contratos de canal).
- **agents/** (110): `media` (provedores de imagem e LLM, B2, Drive, áudio, free_images), `writer` (roteiro e juízes), `posting` (YouTube, Facebook, Pinterest, facebook_scheduler Playwright), `orchestrator` (agentes Gemini), `mcp`, `rag`.
- **modules/** (86): blocos reutilizáveis do reel: `reel_artefact` (busca, budget, net_retry, still_guard), `reel_qa` (gate fail-closed), `reel_visual_qa.py`, `reel_grounding`, `reel_hook`, `shot_planner`, `memory` (supermemory), `asset_library`.
- **omni/** (12): runtime da VM: `worker` (entrypoint do compose e do Docker), `config` (brandings.yaml), `publisher`, `pinterest_drip`, contas.
- **utils/** (10): `pipeline_paths` (130 importadores, é a fonte real de caminhos), `process_tools`, `run_ledger`, `gemini_usage`, OCR, `quote_renderer` e o intruso `cursor_recovery`.
- **tools/** (8): CLIs de manutenção (backfill, cleanup, restore_channel_state, audit_google_costs).
- **scripts/** (104): one-offs e experimentos. 75 deles em `scripts/lofi` (probes, lock_/make_/verify_*). Nada aqui é importado pelo runtime.
- **quality/VisualQA_Agent** (8): crítico visual Gemini legado, com seu próprio `config`, `image_generator` e `main`. Ainda é usado via `visual_critic`.
- **channels_config/** (137): um diretório por canal (`page_config.py`, `persona_dna.py`, prompts, store). O `aiwake` é praticamente um app próprio, com tools, tests, models e settings.
- **models_avatar/** (22): serviço de avatares (RunPod/Civitai), com `env.py` e `paths.py` próprios.
- **infra/** (3): workflows RunPod/serverless (JSON + handler).
- **portfolio_showcase/** (6): logger de vitrine, assinante do event bus. Fora do runtime.
- **tests/** (57) + testes dentro de `channels_config/*/tests`. **docs/**, **docker/**, **config/** (brandings.yaml), **assets/**, **Fonts/**, **critic_profiles/**: dados e documentação.

## 2. channel_loader.py e config.py
- **config.py** (891 linhas, 43 importadores): carrega o `.env` (`_load_project_dotenv`), define constantes globais (modelos Gemini, Claude, DeepSeek, Together, chaves, ElevenLabs), preços (`estimate_gemini_image_usd`) e caminhos por página, que ele reexporta de `utils.pipeline_paths`. Também guarda estado mutável global: `bind_active_page()` reescreve `ACTIVE_PAGE`, `PAGE_OUTPUTS_DIR` etc. Tem fallback Windows em `_REFERENCE_AVATAR_WINDOWS` (l.638).
- **channel_loader.py** (1802 linhas, 9 importadores: `engine.runtime`, `engine.cli_run`, `free_images.policy`...): monta o `PageContext` de um canal a partir de `channels_config/<id>/page_config.py`, JSONs e persona (`load_page_context`, `_load_page_config`, `resolve_default_format/avatar_mode`). Também chama Gemini Vision para extrair o style anchor e importa `google_guardrail` e `remote_gpu_manager`, o que dá muito acoplamento para um loader.
- **Sobreposição:** os dois resolvem "página ativa → diretório do canal". `config.bind_active_page` e `config.ACTIVE_PAGE_DIR` duplicam `channel_loader._resolve_channels_config_root` e `pipeline_paths.channel_config_dir`. `_load_page_config` existe 3 vezes (`channel_loader`, `endless_summer_paradise/handler.py`, `core/principles_of_wealth/catalog.py`). Proposta: config = só env e constantes imutáveis; caminhos = `pipeline_paths`; canal = `channel_loader` sem chamadas de LLM.

## 3. Duplicatas, por valor de unificar
1. **Leitura de .env/config:** 55 arquivos chamam `load_dotenv`, incluindo `config.py`, `utils/pipeline_paths._ensure_dotenv`, `agents/posting/pinterest_engine/config.py`, `quality/VisualQA_Agent/config.py`, `models_avatar/env.py`, `agents/media/b2_client.py` e `core/remote_gpu_manager.py`. O `pinterest_engine/config.py` copia funções do `config.py`: `print_dotenv_bootstrap`, `get_best_claude_model`, `_resolve_path` e `_parse_interval_hours`. Helpers de env repetidos: `_bool_env` (config, model_router), `_as_bool` (reel_qa.relevance, omni.config, omni.pinterest_accounts), `_env_float` (google_guardrail, still_guard), `_env_path` (pinterest publisher, pipeline_paths). **Casa única:** `config.py` para o bootstrap do env e um novo `utils/env.py` para os getters tipados (respeitando `OMNI_SKIP_DOTENV`).
2. **Rastreio de custo:** `core/cost_tracker.CostTracker` (estimativa por run, preço Schnell de US$ 0,003 desatualizado), `core/spend_ledger` (ledger real por reel), `modules/reel_artefact/budget` (teto de stills), `utils/gemini_usage` (custo de OCR), `config.note_gemini_flash_call` e `estimate_gemini_image_usd`, e `agents/writer/claude_polish._usd` vs `reel_artefact.budget._usd`. O `agents/media/b2_budget` é de outro domínio (orçamento de B2) e fica separado. **Casa única:** `core/spend_ledger` como fonte de gastos e de preços. O `CostTracker` vira uma fachada que lê do ledger, e o `gemini_usage` grava no ledger.
3. **Roteamento de LLM/texto:** `agents/media/providers/model_router` (SKUs Gemini), `core/cheap_llm` (cadeia barata, sem Gemini), `agents/orchestrator/llm` (helper Gemini), `channels_config/aiwake/models/openrouter`+`llm_factory`, e chamadas Gemini diretas em `agents/writer/script_agent._call_gemini` e `core/reference_reel_engine._call_gemini`. Parsing de JSON duplicado: `script_agent._extract_json_object`, `ocr_paraphrase._extract_json_object` e `freeform_writer._extract_json`. **Casa única:** `agents/media/providers/` (router + cheap_llm lá dentro), com `extract_json` em `providers/json_utils.py`.
4. **Provedores de imagem:** `agents/media/providers/image_provider.get_image_adapter` vs `core/remote_gpu_manager.get_image_adapter`/`generate_image`; `together_image.sanitize_prompt_for_flux` vs `quality/VisualQA_Agent/image_generator.sanitize_prompt_for_flux`; `agents/media/visual_compiler.compile_flux_prompt` vs `VisualQA_Agent/image_generator.compile_flux_prompt`. **Casa única:** `agents/media/providers/` (o adapter de GPU fica só como backend).
5. **Juízes de QA:** `modules/reel_qa` (gate do reel), `modules/reel_visual_qa` (QA de still), `agents/media/providers/visual_eval`, `agents/orchestrator/agents/visual_qa` (wrapper) e `quality/VisualQA_Agent/visual_critic` (Gemini, usado pelo lofi e pelo reel_visual_qa). `_mime_for` repetido em `style_reader`, `vision_agent`, `visual_critic` e `ocr_engine`. **Casa única:** `modules/reel_qa/` (com `judges/`). `quality/` vira um shim e depois é aposentado.
6. **ffmpeg/render:** `core/reel_ffmpeg_render._run`, `core/economic_reel_lofi/assembler.kill_descendant_ffmpeg` vs `utils/process_tools.kill_descendant_ffmpeg`, `core/animator/renderer._resolve_ffmpeg` vs `scripts/patch_last_30_audio._resolve_ffmpeg` vs `core/utils/fingerprint_engine` (`C:\ffmpeg`), e `_ffmpeg` em `core/animator/pipeline` + 4 scripts. Mais 20 arquivos definem helpers ffmpeg (`video_converter`, `brand_composer`, `reel_sequence_engine`, `reel_qa/mp4_gate`, `aiwake/media/audio`). **Casa única:** `utils/ffmpeg.py` (resolve o binário, faz run com timeout e kill), reaproveitando `process_tools`.
7. **Fontes/caminhos:** `_load_font` em 4 lugares (`brand_composer`, `aiwake/media/renderer`, `animator/vision/head_analyzer`, `quote_renderer`) e listas `C:\Windows\Fonts` em cerca de 12 arquivos. `_engine_root` em 5 lugares (`remote_gpu_manager`, lofi `pipeline`/`test_preview`, 2 probes), `repo_root` em `omni.config` e `shot_planner.entities`, `_outputs_root` em `post_planner` e `asset_library`. **Casa única:** `utils/pipeline_paths` (`repo_root`, `find_font()` com env `FONT_DIRS` e a pasta `Fonts/` do repo).
8. **Logging:** `logging.basicConfig` em 44 arquivos; `_setup_logging`/`_configure_logging` em 8 (`publish_existing`, `schedule_youtube`, `esp_main`, `wealth_main`, `models_avatar.cli`, 3 em utils). Loggers próprios em `engine/log.py`, `facebook_scheduler/logger.py` e `portfolio_logger`. **Casa única:** `engine/log.setup_logging()` chamado só nos entrypoints.
9. **Retry/backoff:** `modules/reel_artefact/net_retry` (DNS), `modules/reel_qa/retry` (regerar still, que é outro conceito), e loops ad hoc em `together_image`, `image_provider`, `gemini_utils`, `b2_budget`, `drive_source`, `pinterest publisher`, `reel_artefact/fetch` e `ocr_engine`. **Casa única:** `utils/retry.py`, aproveitando o `net_retry` (sem retry de erro de billing).
10. **Upload Drive/B2:** `agents/media/b2_client` é o canônico, mas `scripts/upload_clips_to_b2`, `upload_library_to_b2`, `upload_last_30_master_mei_b2` e `_repair_postplanner_b2` têm lógica própria. `agents/media/drive_source` vs `scripts/sync_drive_assets`. Ledger de JSON local: `b2_budget._load_state/_save_state/_read_cache/_write_cache` vs `principles_of_wealth.pipeline._load_state/_save_state`. **Casa única:** `agents/media/b2_client` e `drive_source`, com os scripts virando CLIs finas.
11. **Config de canal:** `persona_dna.py` repetido em 6 canais com as mesmas funções (`contextual_cta_keyword`, `persona_context_block`, `philosophy_block`, `random_cta_keyword`, `visual_style_block`) e cópia em `agents/media/persona_dna`. `load_settings` em `aiwake.settings` e `memory.config`. **Casa única:** `agents/media/persona_dna` com os dados por canal em JSON/YAML.
12. **TTS:** `engine/voice.py` (reels), `core/economic_reel_lofi/voiceover.py`, `agents/media/audio_engine.py`, `core/reference_reel_engine` e `core/animator/voice/registry`, mais `_tts` em 2 tools do aiwake. **Casa única:** `agents/media/audio_engine` como cliente ElevenLabs, com os outros chamando ele.
13. **Menores:** `_utc_now_iso`/`_utc_now`/`_now`/`_today` (cerca de 12 lugares), `_tokens` (9), `split_sentences` (`aiwake.contracts`, `reel_qa.beats`, `shot_planner.build`), `content_library.load_library/save_library` vs `asset_library.load_library/save_library`, `youtube_publisher.sanitize_youtube_description` vs `aiwake library_sanitize`, `advance_slot`/`cadence_label` (`youtube_publisher`/`aiwake` vs `principles_of_wealth.schedule`). **Casa única:** `utils/text.py` e `utils/timeutil.py`.

## 4. Candidatos a morto
**Mortos comprovados** (nenhum import, nenhuma referência em docs, compose, workflows, scheduler ou testes; só têm `__main__` de one-off):
- `utils/cursor_recovery.py`: recuperação de histórico do Cursor, sem relação com o engine.
- `scripts/repair_python_utf8.py` e `scripts/sanitize_python_sources.py`: one-offs de encoding de 05/10.
- `scripts/generate_lofi_bgm_test.py`: teste de BGM sem referência.

**Duvidosos** (sem import, mas com CLI, doc ou referência indireta, então ficam):
- `core/animator/factory/generate_concepts.py` (CLI, 0 referências) e `one_shot_factory.py` (citado em `docs/architecture/main-module-map.md`).
- `agents/posting/scheduler_engine.py` (`scripts/README`), `publish_existing.py` e `pinterest_oauth.py` (`COMMAND_REFERENCE`), `pinterest_main.py` (`.env.example` e `run_pinterest_local.ps1`), `facebook_scheduler/main`, `record_mode` e `reels_scheduler` (entrypoints Playwright).
- `portfolio_showcase/core/portfolio_log.py` (listado em `docs/architecture/DEPRECATED.md`, é o candidato mais forte a apagar depois).
- `scripts/inspect_chroma_db.py` (README), quase todo o `scripts/lofi/*` (75 one-offs de experimento: probes, make_*_reuse2/3, lock_*) e `scripts/wan_*`.
- `channels_config/*/tools/*` (one-offs do aiwake e do AK), `wealth_main.py` e `esp_main.py` (doc), `quality/VisualQA_Agent/main.py`.
- `core/reel_ffmpeg_render.py` só é importado por testes, mas é usado de forma lazy por `core/reel_sequence_engine` (vivo).
- Os testes dentro de `channels_config/*/tests` são coletados pelo pytest, então não são mortos.

## 5. Caminhos de máquina (fora de tests)
- **G:\ (Drive do Diogo):** `utils/pipeline_paths.py:112` (default do OUTPUT_PATH), `config.py:639` (avatar de referência), `channels_config/principles_of_wealth_finance_economics/page_config.py:41`, `scripts/sync_drive_assets.py:299`, `tools/audit_google_costs.py:13` (docstring), `scripts/repair_python_utf8.py:9` (docstring), `agents/media/content_source.py:48`, `agents/posting/pinterest_engine/library_source.py:58` e `core/animator/renderer.py:34` (só docstrings).
- **C:\dev / C:\Users:** `channels_config/ancient_knowledge/page_config.py:134,138` (`C:\dev\omni-engine`), `channels_config/aiwake/tools/apply_final_youtube.py:33-34` (perfil de usuário Windows).
- **C:\ffmpeg:** `core/utils/fingerprint_engine.py:67`, `scripts/patch_last_30_audio.py:70`.
- **C:\Windows\Fonts:** `agents/posting/pinterest_engine/image_transformer.py:90-95,107-110`, `utils/quote_renderer.py:610`, `core/reel_sequence_engine.py:66-68` (via WINDIR), `agents/media/brand_composer.py:699,930`, `channels_config/aiwake/animator_bridge.py:835-836`, `channels_config/aiwake/aiwake_config.yaml:388-389`, `channels_config/ancient_knowledge/tools/rebuild_silent_reels.py:44-46`, `core/animator/asset_generator.py:394-395`, `core/animator/factory/create_environment.py:452-453`, `core/animator/pipeline.py:1305-1306`, `core/animator/thumbnail_generator.py:19-21`, `core/economic_reel_lofi/caption_style_lofi.py:145`, `scripts/lofi/probes/vo_pace_probe.py:57`.
- **D:/clips (exemplo):** `agents/posting/facebook_scheduler/reels_scheduler.py:26-28`.
- **Dados versionados com G:\\:** `core/economic_reel_lofi/store/lofi_performance_events.json` (107 ocorrências), `lofi_coverage_flags.jsonl` (l.1-39), 3 `locked_script_*.json` (10 cada) e `channels_config/aiwake/assets/bgm/library_manifest.json` (13).
- **Docs:** `docs/runbooks/google-drive.md:72`, `supermemory.md:114`, `ADR 0007:9`, `facebook_scheduler/README.md:57`.
- **Testes:** `tests/test_drive_source.py:29,35,41,701` e outros 9 arquivos (fixtures, aceitáveis).

## 6. Pacote omni/: dá para dissolver?
**O que faz** (12 arquivos, 1519 linhas): é a camada de publicação e o runtime da VM.
- `worker.py` (247): entrypoint `--role engine|scheduler` e loop de 7 a 9h.
- `pinterest_drip.py`: drip por conta sobre o `pinterest_engine`.
- `accounts.py`: tokens Pinterest por branding e `remember_rotated_token`, que reescreve o `.env`.
- `pinterest_accounts.py` e `config.py`: leem `config/pinterest_accounts.yaml` e `brandings.yaml`, e `config.py` tem o `repo_root`.
- `publisher.py`: interface com retry e idempotência via `FileIdempotencyStore`.
- `registry.py`, `models.py` (Account, Content).
- `adapters/pinterest.py` e `adapters/youtube.py`: wrappers finos dos publishers de `agents/posting`.

**Inventário de dependências**
- **Entrypoint:** `Dockerfile:38` (CMD `python -m omni.worker --role scheduler`) e `docker-compose.yml:48,78` (engine e scheduler). O `.github/workflows/deploy.yml` não cita `omni.worker` diretamente: ele copia o `docker-compose.yml` para a VM (l.129), então o comando vale na VM a partir do compose copiado.
- **Imports em código de produção (5):** `engine/runtime.py:55` (`omni.adapters.youtube.publish_envelope`), `agents/posting/pinterest_main.py:165-166`, `agents/posting/pinterest_engine/facebook_queue.py:282`, `agents/posting/pinterest_engine/articles.py:524` (`omni.config.repo_root`). Dentro do omni: `pinterest_drip` → `pinterest_engine.articles/photos`, o que forma um ciclo `omni` ↔ `pinterest_engine` resolvido com imports lazy.
- **Testes:** `tests/test_pinterest_vm.py` (imports + 7 `monkeypatch.setattr("omni.worker.*" / "omni.accounts.repo_root" / "omni.pinterest_drip.publish_account")`), `test_pinterest_publisher_fixes.py` (4 monkeypatch em `omni.worker`), `test_omni_config.py`, `test_omni_publisher.py`. Monkeypatch por string não segue re-export: com shim, o patch no nome antigo não afeta o módulo novo.
- **Ruff:** `pyproject.toml:7` (`src`), `:68` (`known-first-party`) e comentários `:13,:18`. O omni/ fica de propósito fora dos per-file-ignores, ou seja, no regime estrito (F401/F841/I001). Ao mover, o destino precisa ficar estrito também, e `core/`, `modules/`, `utils/` não estão nas ignores, então isso já acontece.
- **CI:** comentários em `validate.yml:29,47` (a lista de testes do omni).
- **Docs:** `README.md:26-27,52`, `docs/deploy.md:22,37`, `docs/architecture/overview.md:29`, `ADR 0002:18`, `REPO_LAYOUT.md:45` ("omni/ stays the package") e `AGENTS.md:10`, que diz "Do not rename or move: omni/". Essa regra precisa ser revogada explicitamente pelo Diogo.
- **Logger:** `modules/memory/*` usa `logging.getLogger("omni.memory")`. É só um nome de logger, não depende do pacote, e pode ficar.
- **Estado persistente na VM:** `publisher.default_ledger_path()` → `$OUTPUT_PATH/_omni/publish_ledger.json` (volume `./outputs`) e `OMNI_LEDGER_PATH`. Variáveis `OMNI_ROLE`, `OMNI_ROOT`, `OMNI_IMAGE_TAG`, `_OMNI_APPLIED_PINTEREST_TOKEN/REFRESH` (gravadas no `.env` montado). Tudo isso **precisa manter o nome**, senão a idempotência zera e pins podem sair duplicados.

**Destino proposto, por responsabilidade**
| módulo | destino | colisão |
|---|---|---|
| worker.py | `core/worker.py` | nenhuma |
| pinterest_drip.py | `core/publishing/pinterest_drip.py` | nenhuma |
| publisher.py, registry.py | `core/publishing/publisher.py`, `registry.py` | `core/animator/voice/registry.py` (outro subpacote, sem conflito real) |
| models.py | `core/publishing/models.py` | `modules/reel_qa/models.py` (só homônimo) |
| adapters/* | `core/publishing/adapters/` | nenhuma |
| accounts.py, pinterest_accounts.py | `modules/accounts/` (ou `core/publishing/accounts.py`) | nenhuma |
| config.py (brandings) | `core/publishing/brandings.py` | **evitar o nome `config.py`**: já existem `config.py` na raiz, `core/economic_reel_lofi/config.py`, `modules/{memory,reel_qa,reel_grounding}/config.py` |
| config.repo_root | `utils/pipeline_paths.repo_root` | duplica `shot_planner.entities.repo_root` (unificar) |

**Risco: médio.** O código é pequeno e bem testado, mas: (a) é o processo que publica em produção, e um erro no entrypoint para o scheduler depois do deploy automático; (b) monkeypatch por string nos testes; (c) ciclo de imports com `pinterest_engine`; (d) estado persistente com nome `_omni`; (e) a regra do `AGENTS.md` e da documentação de layout. O risco cai para baixo se o entrypoint ficar estável durante a transição.

**Recomendação: N PRs em etapas, com shims temporários em omni/.**
1. PR-O1: criar `core/publishing/*`, `core/worker.py`, `modules/accounts/*` como cópias movidas (`git mv` + `omni/*` vira re-export `from core.publishing.x import *` e `omni/worker.py` chama `core.worker.main`). Testes migram para os novos caminhos, incluindo as strings de monkeypatch. Ledger, envs e o nome `_omni` intocados. Dockerfile e compose ainda com `omni.worker`. Deploy na VM e conferir o próximo pin.
2. PR-O2: imports de produção (`engine/runtime`, `pinterest_main`, `facebook_queue`, `articles`) apontam para os novos módulos, `repo_root` vai para `pipeline_paths`, e o pyproject e as docs são atualizados (com o OK do Diogo para mudar o `AGENTS.md`).
3. PR-O3: Dockerfile e compose passam a usar `python -m core.worker`. Deploy e conferência da VM (Automação & Nuvem).
4. PR-O4, depois de 1 ou 2 ciclos de scheduler OK: apagar `omni/` (shims) e tirar `omni` do `src`/`known-first-party`. Um `rg "omni\."` precisa voltar só o logger `omni.memory` e os nomes de env/ledger.

Um PR único não é recomendado, porque mistura a troca do entrypoint de produção com a migração de testes, e um deploy automático quebrado para o scheduler. Já "não fazer" só se justifica se o Diogo quiser preservar a regra do `AGENTS.md`. Tecnicamente não há impedimento.

## 7. Sequência de PRs (cada um sem mudar comportamento)
1. **PR-A, lixo comprovado:** apagar os 4 mortos da seção 4. Testes verdes e dry-run de reel.
2. **PR-B, caminhos via env:** `OMNI_DRIVE_ROOT`, `REFERENCE_AVATAR_PATH`, `FFMPEG_BINARY` e `FONT_DIRS` em `pipeline_paths`, mantendo os defaults atuais quando a env existir. Trocar `C:\dev`/`C:\Users` por caminhos relativos ao repo e um único `find_font()` usando `Fonts/`. Atualizar `.env.example`.
3. **PR-C, env única:** `utils/env.py` (bool, float, path) e um só `load_dotenv` em `config.py`. O `pinterest_engine/config` passa a reexportar do `config` em vez de copiar.
4. **PR-D, ffmpeg único:** `utils/ffmpeg.py` (resolve, run com timeout, kill). Remover a cópia de `kill_descendant_ffmpeg` do assembler.
5. **PR-E, custo:** `CostTracker` lê os preços do `spend_ledger` e o `gemini_usage` grava no ledger. Testes de custo iguais antes e depois.
6. **PR-F, providers:** `model_router` + `cheap_llm` + `extract_json` dentro de `agents/media/providers`. Shims de reexport nos caminhos antigos.
7. **PR-G, imagem e QA:** `sanitize_prompt_for_flux`, `compile_flux_prompt` e `get_image_adapter` com um dono só. `quality/VisualQA_Agent` vira shim de `modules/reel_qa/judges`.
8. **PR-H, persona_dna:** funções comuns ficam em `agents/media/persona_dna`, os canais só com dados.
9. **PR-I, logging e retry:** `setup_logging()` só nos entrypoints e `utils/retry.py`.
10. **PR-J, README:** diagrama (main → engine → providers → reel_qa → posting → omni.worker) e quickstart em modo mock sem chaves. O merge do #33 vem antes, com o OK do Diogo.

11. **PR-O1..O4:** dissolver omni/ (seção 6).

O histórico do git nunca é reescrito: sem force-push nem rebase do main.
Regra em todos: shim de reexport no local antigo, sem apagar o que for duvidoso, CI verde e prova de reel em dry-run/mock.
