# Iri-shka: Voice AI Assistant | Голосовой ИИ-Ассистент Ири-шка

---

## English

Iri-shka is a multimodal voice-first AI assistant designed for intuitive interaction, task management, and **AI autonomy through an offline-first paradigm**. She leverages local and open-source AI models for speech-to-text (Whisper), text-to-speech (Bark), and language understanding (Ollama with models like Phi, Gemma, Qwen and others), ensuring core functionalities operate without requiring an internet connection once set up. The application features a Tkinter-based graphical user interface (GUI), system tray integration, and Telegram bot support (Telegram itself requires internet).

**Core Philosophy: Offline First & AI Autonomy**

*   **Local Processing:** Speech recognition (Whisper), speech synthesis (Bark), and core reasoning (Ollama LLMs) are processed locally on your machine.
*   **Data Privacy:** By keeping processing local, your conversations and data remain private.
*   **Offline Capability:** Once models are downloaded and Ollama is running with a local model, Iri-shka's primary voice interaction and thinking capabilities do not depend on an active internet connection.
*   **AI Autonomy:** The goal is to provide an assistant that can operate and assist effectively even in disconnected environments, fostering greater independence for the AI.
    *(Note: Features like web search or Telegram bot communication inherently require internet access.)*

**Core Features:**

*   **Voice Interaction:** Speak to Iri-shka and receive spoken responses, primarily offline.
*   **Local First AI Stack:** Prioritizes local models (Bark, Whisper, Ollama) for privacy and offline operation.
*   **Multimodal Input:**
    *   Voice input via microphone (offline capable).
    *   Text input via Telegram (requires internet for Telegram).
*   **GUI:**
    *   Real-time chat history display.
    *   Kanban board for Iri-shka's internal tasks (Pending, In Process, Completed).
    *   Calendar view with event display.
    *   To-Do list management.
    *   Component status indicators (Internet, Hearing, Voice, Mind, etc.).
    *   GPU monitoring (if NVIDIA GPU and pynvml are available).
    *   Theming (Light/Dark) and font size customization, controllable via voice/text commands.
*   **System Tray Integration:**
    *   Show/Hide application window.
    *   Controls for loading/unloading TTS (Bark) and STT (Whisper) models.
    *   Controls for starting/stopping the Telegram bot.
    *   Exit application.
*   **Telegram Bot Interface (Optional, requires internet):**
    *   Interact with Iri-shka via text messages on Telegram (admin user restricted).
    *   Start/Stop bot from the system tray.
    *   Status indicator in the GUI.
*   **State Management:**
    *   Persistent user state (preferences, todos, calendar events, birthdays).
    *   Persistent assistant state (internal tasks, persona details).
    *   Chat history saved locally.
*   **LLM Integration:** Uses Ollama to connect to various locally hosted large language models for natural language understanding and response generation.
*   **Bilingual Support:**
    *   The assistant can understand and respond in English and Russian (based on Bark voice presets and LLM instructions).
    *   The GUI is primarily in English, but Iri-shka's responses can be in the detected language.

**Tech Stack:**

*   **Python 3**
*   **AI Models (Local):**
    *   Speech-to-Text: OpenAI Whisper (via `openai-whisper` library)
    *   Text-to-Speech: Suno Bark (via `transformers` library)
    *   LLM: Ollama (e.g., `qwen2.5vl:7b`, `phi4`, etc.)
*   **GUI:** Tkinter (ttk, tkcalendar)
*   **Audio:** PyAudio, SoundDevice
*   **System Tray:** pystray, Pillow
*   **Telegram Bot (Optional):** python-telegram-bot
*   **Other Key Libraries:** NumPy, Requests, python-dotenv, NLTK (for TTS sentence tokenization)

**Setup & Installation:**

1.  **Clone the Repository:**
    ```bash
    git clone github.com/vovsn/iri-shka
    cd python_tts_test 
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *   **Note on PyAudio:** You might need to install system dependencies for PyAudio (e.g., `portaudio19-dev` on Debian/Ubuntu: `sudo apt-get install portaudio19-dev python3-pyaudio`).
    *   **Note on PyTorch (for Bark/Whisper):** `transformers[torch]` in `requirements.txt` should install PyTorch. For specific CUDA versions, consider installing PyTorch manually first from [pytorch.org](https://pytorch.org/).

4.  **Set up Local AI Models (Essential for Offline First):**
    *   **Ollama:**
        *   Install Ollama from [ollama.ai](https://ollama.ai/).
        *   Pull your desired LLM: `ollama pull qwen2.5vl:7b` (or your chosen model in `config.py`). This step requires internet.
        *   Ensure the Ollama server is running locally.
    *   **Bark (TTS) & Whisper (STT):**
        *   The first time you run the application, these models will be downloaded by the `transformers` and `openai-whisper` libraries respectively if not found in their cache. This initial download requires an internet connection.
        *   **For true offline Bark:** Pre-download the `suno/bark-small` model (or your chosen variant) and place its contents into the `python_tts_test/bark/` directory. The application is configured to look here first (`HF_HUB_OFFLINE=1` is used). The `bark` folder should contain files like `pytorch_model.bin`, `config.json`, `tokenizer.json`, etc.
        *   Whisper models are typically cached by the library in a user directory.

5.  **Configure Environment Variables:**
    *   Copy `.env.example` to `.env`:
        ```bash
        cp .env.example .env
        ```
    *   Edit `.env` and fill in your details:
        *   `OLLAMA_API_URL`: (Defaults to `http://localhost:11434/api/generate`, usually correct for local Ollama).
        *   `TELEGRAM_BOT_TOKEN`: (Optional) Your Telegram Bot API token.
        *   `TELEGRAM_ADMIN_USER_ID`: (Optional) Your numerical Telegram User ID.

6.  **Run the NLTK Downloader (One-time, requires internet if 'punkt' is missing):**
    The application attempts to download the 'punkt' tokenizer for NLTK automatically. If this fails, run this in a Python interpreter:
    ```python
    import nltk
    nltk.download('punkt')
    ```

7.  **Run the Application:**
    ```bash
    python main.py
    ```

**Usage:**

*   **GUI:**
    *   Use the "Speak" button (or press Spacebar when not typing in a text field) to talk to Iri-shka.
    *   Monitor system status and Iri-shka's tasks.
    *   Change theme/font size via voice commands (e.g., "change to dark theme", "make text bigger").
*   **System Tray:**
    *   Right-click the tray icon for options.
*   **Telegram (Requires Internet for Telegram Service):**
    *   If configured and started, message your bot from the admin Telegram account.
    *   Use `/start` to greet the bot.

**Known Issues / Future Enhancements:**

*   (Add any known limitations or ideas for future work here)
*   More robust error handling for model loading and API calls.
*   Voice message support for Telegram.
*   Allow Iri-shka to initiate actions based on her internal state or schedule.

---

## Русский

Ири-шка — это мультимодальный голосовой ИИ-ассистент, разработанный для интуитивного взаимодействия, управления задачами и **обеспечения автономии ИИ через парадигму "сначала офлайн" (offline-first)**. Она использует локальные ИИ-модели с открытым исходным кодом для преобразования речи в текст (Whisper), текста в речь (Bark) и понимания естественного языка (Ollama с моделями вроде Qwen или Phi-3), гарантируя, что основные функции работают без необходимости подключения к интернету после первоначальной настройки. Приложение имеет графический пользовательский интерфейс (GUI) на основе Tkinter, интеграцию с системным треем и поддержку Telegram-бота (сам Telegram требует интернет).

**Ключевая Философия: Сначала Офлайн и Автономия ИИ**

*   **Локальная Обработка:** Распознавание речи (Whisper), синтез речи (Bark) и основное логическое мышление (LLM через Ollama) обрабатываются локально на вашем компьютере.
*   **Конфиденциальность Данных:** Благодаря локальной обработке ваши разговоры и данные остаются приватными.
*   **Возможность Работы Офлайн:** После загрузки моделей и запуска Ollama с локальной моделью, основные возможности Ири-шки по голосовому взаимодействию и мышлению не зависят от активного интернет-соединения.
*   **Автономия ИИ:** Цель — предоставить ассистента, который может эффективно работать и помогать даже в условиях отсутствия связи, способствуя большей независимости ИИ.
    *(Примечание: Функции, такие как веб-поиск или связь через Telegram-бота, по своей природе требуют доступа в интернет.)*

**Основные Возможности:**

*   **Голосовое Взаимодействие:** Говорите с Ири-шкой и получайте голосовые ответы, преимущественно в офлайн-режиме.
*   **Локальный Стек ИИ:** Приоритет локальным моделям (Bark, Whisper, Ollama) для конфиденциальности и работы офлайн.
*   **Мультимодальный Ввод:**
    *   Голосовой ввод через микрофон (поддерживает офлайн).
    *   Текстовый ввод через Telegram (требует интернет для Telegram).
*   **GUI (Графический Интерфейс):**
    *   Отображение истории чата в реальном времени.
    *   Kanban-доска для внутренних задач Ири-шки (Ожидание, В процессе, Завершено).
    *   Календарь с отображением событий.
    *   Управление списком дел (To-Do).
    *   Индикаторы состояния компонентов (Интернет, Слух, Голос, Разум и т.д.).
    *   Мониторинг GPU (если доступна видеокарта NVIDIA и pynvml).
    *   Настройка тем (Светлая/Темная) и размера шрифта с помощью голосовых/текстовых команд.
*   **Интеграция с Системным Треем:**
    *   Показать/Скрыть окно приложения.
    *   Управление загрузкой/выгрузкой моделей TTS (Bark) и STT (Whisper).
    *   Управление запуском/остановкой Telegram-бота.
    *   Выход из приложения.
*   **Интерфейс Telegram-бота (Опционально, требует интернет):**
    *   Взаимодействие с Ири-шкой через текстовые сообщения в Telegram (доступ ограничено администратором).
    *   Запуск/Остановка бота из системного трея.
    *   Индикатор состояния в GUI.
*   **Управление Состоянием:**
    *   Сохранение состояния пользователя (предпочтения, задачи, события календаря, дни рождения).
    *   Сохранение состояния ассистента (внутренние задачи, детали персоны).
    *   История чата сохраняется локально.
*   **Интеграция с LLM:** Использует Ollama для подключения к различным локально размещенным большим языковым моделям для понимания естественного языка и генерации ответов.
*   **Двуязычная Поддержка:**
    *   Ассистент может понимать и отвечать на английском и русском языках (на основе голосовых пресетов Bark и инструкций для LLM).
    *   GUI преимущественно на английском, но ответы Ири-шки могут быть на распознанном языке.

**Технологический Стек:**

*   **Python 3**
*   **ИИ-Модели (Локальные):**
    *   Речь-в-Текст: OpenAI Whisper (через библиотеку `openai-whisper`)
    *   Текст-в-Речь: Suno Bark (через библиотеку `transformers`)
    *   LLM: Ollama (например, `qwen2.5vl:7b`, `phi3` и др.)
*   **GUI:** Tkinter (ttk, tkcalendar)
*   **Аудио:** PyAudio, SoundDevice
*   **Системный Трей:** pystray, Pillow
*   **Telegram Бот (Опционально):** python-telegram-bot
*   **Другие Ключевые Библиотеки:** NumPy, Requests, python-dotenv, NLTK (для токенизации предложений TTS)

**Установка и Настройка:**

1.  **Клонируйте Репозиторий:**
    ```bash
    git clone <URL-вашего-репозитория>
    cd python_tts_test
    ```

2.  **Создайте Виртуальное Окружение (Рекомендуется):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # В Windows: venv\Scripts\activate
    ```

3.  **Установите Зависимости:**
    ```bash
    pip install -r requirements.txt
    ```
    *   **Примечание по PyAudio:** Возможно, потребуется установка системных зависимостей для PyAudio (например, `portaudio19-dev` для Debian/Ubuntu: `sudo apt-get install portaudio19-dev python3-pyaudio`).
    *   **Примечание по PyTorch (для Bark/Whisper):** `transformers[torch]` в `requirements.txt` должен установить PyTorch. Для конкретных версий CUDA рассмотрите возможность ручной установки PyTorch с [pytorch.org](https://pytorch.org/).

4.  **Настройте Локальные ИИ-Модели (Обязательно для Offline First):**
    *   **Ollama:**
        *   Установите Ollama с [ollama.ai](https://ollama.ai/).
        *   Загрузите желаемую LLM: `ollama pull qwen2.5vl:7b` (или модель, указанную в `config.py`). Этот шаг требует интернета.
        *   Убедитесь, что сервер Ollama запущен локально.
    *   **Bark (TTS) и Whisper (STT):**
        *   При первом запуске приложения эти модели будут загружены библиотеками `transformers` и `openai-whisper` соответственно, если они не найдены в их кеше. Эта первоначальная загрузка требует подключения к интернету.
        *   **Для полноценной офлайн-работы Bark:** Предварительно загрузите модель `suno/bark-small` (или выбранный вами вариант) и поместите ее содержимое в директорию `python_tts_test/bark/`. Приложение настроено сначала искать модели там (используется `HF_HUB_OFFLINE=1`). Папка `bark` должна содержать файлы вроде `pytorch_model.bin`, `config.json`, `tokenizer.json` и т.д.
        *   Модели Whisper обычно кешируются библиотекой в пользовательской директории.

5.  **Настройте Переменные Окружения:**
    *   Скопируйте `.env.example` в `.env`:
        ```bash
        cp .env.example .env
        ```
    *   Отредактируйте `.env` и введите ваши данные:
        *   `OLLAMA_API_URL`: (По умолчанию `http://localhost:11434/api/generate`, обычно корректно для локального Ollama).
        *   `TELEGRAM_BOT_TOKEN`: (Опционально) API токен вашего Telegram-бота.
        *   `TELEGRAM_ADMIN_USER_ID`: (Опционально) Ваш числовой ID пользователя Telegram.

6.  **Загрузите Ресурсы NLTK (Однократно, требует интернет, если 'punkt' отсутствует):**
    Приложение пытается автоматически загрузить токенизатор 'punkt' для NLTK. Если это не удастся, выполните в интерпретаторе Python:
    ```python
    import nltk
    nltk.download('punkt')
    ```

7.  **Запустите Приложение:**
    ```bash
    python main.py
    ```

**Использование:**

*   **GUI:**
    *   Используйте кнопку "Speak" (или нажмите Пробел, когда не печатаете в текстовом поле), чтобы говорить с Ири-шкой.
    *   Отслеживайте состояние системы и задачи Ири-шки.
    *   Изменяйте тему/размер шрифта голосовыми командами (например, "смени тему на темную", "сделай текст больше").
*   **Системный Трей:**
    *   Кликните правой кнопкой мыши по иконке в трее для вызова меню.
*   **Telegram (Требует Интернет для сервиса Telegram):**
    *   Если настроено и запущено, отправляйте сообщения вашему боту с аккаунта администратора Telegram.
    *   Используйте `/start` для приветствия бота.

**Известные Проблемы / Будущие Улучшения:**

*   (Добавьте сюда известные ограничения или идеи для будущей работы)
*   Более надежная обработка ошибок при загрузке моделей и вызовах API.
*   Поддержка голосовых сообщений для Telegram.
*   Возможность для Ири-шки инициировать действия на основе ее внутреннего состояния или расписания.