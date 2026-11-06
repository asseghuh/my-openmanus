let currentEventSource = null;
let aiMessageDiv = null;
let finalAnswer = null;
let thoughtQuote = null;
let chatMessages = null;
let chat_state = 'none';
let processStepCount = 0;
let lastThinkContent = '';
let currentTaskId = null;
let sessionCache = [];

// 重置一轮任务的过程块全局状态（新对话/历史回放前调用）
function resetProcessState() {
    aiMessageDiv = null;
    finalAnswer = null;
    thoughtQuote = null;
    processStepCount = 0;
    lastThinkContent = '';
}

function getMarkedText(text) {
    return DOMPurify.sanitize(marked.parse(text))
}

// 去掉日志前缀，得到模型真正的思考文本
function stripThoughtPrefix(text) {
    return String(text || '')
        .replace(/^✨\s*\w+'s thoughts:\s*/i, '')
        .trim();
}

// 识别工具日志、逐步 dump 等非用户面内容，避免写入聊天主区域
function isInternalProcessText(text) {
    const value = String(text || '').trim();
    if (!value) return true;
    return [
        /Tool ['"][^'"]+['"] completed its mission/i,
        /Observed output of cmd/i,
        /Executing (step|tool|action)/i,
        /\w+ selected \d+ tools? to use/i,
        /Activating tool:/i,
        /Special tool .+ has completed the task/i,
        /^Step \d+:/m,
        /Terminated: Reached max steps/i,
        /Image returned\.?/i,
        /Disconnected from/i,
        /Connected to (MCP|server)/i,
        /emit_final_answer_len=/i,
        /user_facing_len=/i,
    ].some((pattern) => pattern.test(value));
}

// 从过程块里倒序找最后一条可读思考（兼容 think 事件漏标、最后一步 thoughts 为空）
function lastThinkFromBlock() {
    if (!thoughtQuote) return '';
    const lines = thoughtQuote.querySelectorAll('.thinking-message');
    for (let i = lines.length - 1; i >= 0; i--) {
        const text = stripThoughtPrefix(lines[i].textContent);
        if (text && !isInternalProcessText(text)) {
            return text;
        }
    }
    return '';
}

// 优先展示用户可读答复；内部过程日志只留在折叠块里
function pickUserFacingText(candidate, thinkText) {
    const options = [candidate, stripThoughtPrefix(thinkText), lastThinkFromBlock()];
    for (const item of options) {
        if (item && !isInternalProcessText(item)) {
            return item;
        }
    }
    return '';
}

function renderFinalAnswer(candidate, thinkText) {
    const display = pickUserFacingText(candidate, thinkText);
    ensureThoughtBlock(false);
    if (display && finalAnswer) {
        try {
            finalAnswer.innerHTML = getMarkedText(display);
        } catch (e) {
            finalAnswer.textContent = display;
        }
    }
    updateThoughtHeader(true);
    collapseThoughtBlock();
}

function showErrorToast(message) {
    const toastEl = document.getElementById('errorToast');
    const toastBody = toastEl.querySelector('.toast-body');
    toastBody.textContent = message;

    const toast = new bootstrap.Toast(toastEl);
    toast.show();
}

function toggle_chat_state(state) {
    chat_state = state
    document.getElementById('send-spinner').style.display = chat_state
}

function createChat() {
    const isLongThought = document.getElementById('longThoughtCheckbox').checked;
    const promptInput = document.getElementById('messageInput');
    const prompt = promptInput.value.trim();

    if (!prompt) {
        showErrorToast("Please enter a valid prompt");
        promptInput.focus();
        return;
    }

    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }
    // 开启新一轮任务前重置过程块，防止事件写入上一轮的块
    resetProcessState();

    const payload = { prompt };
    if (currentTaskId) {
        payload.task_id = currentTaskId;
    }

    fetch('/tasks', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => { throw new Error(err.detail || 'Request failed') });
            }
            return response.json();
        })
        .then(data => {
            if (!data.task_id) {
                throw new Error('Invalid task ID');
            }
            currentTaskId = data.task_id;
            addMessage(prompt, 'user');
            setupSSE(data.task_id, isLongThought);
            promptInput.value = '';
            refreshSessionList();
        })
        .catch(error => {
            showErrorToast(error.message)
            toggle_chat_state('none');
            console.error('Failed to create task:', error);
        });
}

function setupSSE(taskId, isLongThought) {
    let retryCount = 0;
    const maxRetries = 3;
    const retryDelay = 2000;
    let lastResultContent = '';

    function connect() {
        const eventSource = new EventSource(`/tasks/${taskId}/events`);
        currentEventSource = eventSource;

        const finishFromServer = (resultText) => {
            lastResultContent = resultText || lastResultContent || lastThinkContent || '';
            renderFinalAnswer(lastResultContent, lastThinkContent);
            scrollView();
            toggle_chat_state('none');
        };

        const handleEvent = (event, type) => {
            try {
                const data = JSON.parse(event.data);
                const text = data.result || data.message || '';
                // 过程事件一律进入折叠块，绝不作为独立聊天气泡
                ensureThoughtBlock(isLongThought);
                if (type === 'think') {
                    const stripped = stripThoughtPrefix(text);
                    if (stripped) lastThinkContent = stripped;
                }
                if (type === 'log' && text.indexOf('Executing step') > -1) {
                    processStepCount++;
                    updateThoughtHeader(false);
                }
                appendThoughtLine(text);
                // 模型已给出文字且不再继续检索：立刻展示，不等 MCP 清理
                const noMoreTools = type === 'tool' && /selected\s+0\s+tools/i.test(text);
                const ending = /terminate/i.test(text) && (
                    /selected/i.test(text) ||
                    /prepared/i.test(text) ||
                    /Activating tool/i.test(text) ||
                    /Special tool/i.test(text)
                );
                if (lastThinkContent && (noMoreTools || ending)) {
                    finishFromServer(lastThinkContent);
                }
                scrollView();
            } catch (e) {
                console.error(`Error handling ${type} event:`, e);
            }
        };

        const eventTypes = ['think', 'tool', 'act', 'log', 'run', 'message'];
        eventTypes.forEach(type => {
            eventSource.addEventListener(type, (event) => handleEvent(event, type));
        });

        eventSource.addEventListener('result', (event) => {
            try {
                const data = JSON.parse(event.data);
                finishFromServer(data.result || '');
            } catch (e) {
                console.error('Error handling result event:', e);
            }
        });

        eventSource.addEventListener('complete', (event) => {
            try {
                const data = JSON.parse(event.data);
                finishFromServer(data.result || '');
                eventSource.close();
                currentEventSource = null;
                refreshSessionList();
            } catch (e) {
                console.error('Error handling complete event:', e);
                toggle_chat_state('none');
            }
        });

        eventSource.addEventListener('error', (event) => {
            try {
                if (!event.data) return;
                const data = JSON.parse(event.data);
                // 过程中的工具/解析错误写入折叠块，不断开连接
                if (data.result) {
                    ensureThoughtBlock(isLongThought);
                    appendThoughtLine(data.result);
                    scrollView();
                    return;
                }
                showErrorToast(data.message)
                eventSource.close();
                currentEventSource = null;
                toggle_chat_state('none');
            } catch (e) {
                console.error('Error handling failed:', e);
            }
        });

        eventSource.onerror = (err) => {
            if (eventSource.readyState === EventSource.CLOSED) return;

            console.error('SSE connection error:', err);
            eventSource.close();

            fetch(`/tasks/${taskId}`)
                .then(response => response.json())
                .then(task => {
                    if (task.status === 'completed' || task.status === 'failed') {
                        if (task.status === 'completed') {
                            const turns = getTurns(task);
                            const last = turns[turns.length - 1] || {};
                            finishFromServer(last.result || '');
                            refreshSessionList();
                        } else {
                            console.error(task)
                            showErrorToast(task.error)
                            toggle_chat_state('none');
                        }
                    } else if (retryCount < maxRetries) {
                        retryCount++;
                        showErrorToast(`Connection lost, retrying in ${retryDelay / 1000} seconds (${retryCount}/${maxRetries})`)
                        setTimeout(connect, retryDelay);
                    } else {
                        showErrorToast('Connection lost, please try refreshing the page')
                    }
                })
                .catch(error => {
                    console.error('Task status check failed:', error);
                    if (retryCount < maxRetries) {
                        retryCount++;
                        setTimeout(connect, retryDelay);
                    }
                });
        };
    }

    connect();
}

function loadHistory() {
    fetch('/tasks')
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`request failure: ${response.status} - ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(tasks => {
            applyHistory(tasks)
        })
        .catch(error => {
            console.error('Failed to load history records:', error);
            showErrorToast(error.message)
        });
}

function sessionTitle(task) {
    const value = (task && (task.title || task.prompt) || '').trim();
    return value || '新对话';
}

function sessionUpdatedAt(task) {
    return new Date(task && (task.updated_at || task.created_at) || 0);
}

function getTurns(item) {
    if (item && Array.isArray(item.turns) && item.turns.length) {
        return item.turns;
    }
    const steps = (item && item.steps) || [];
    const resultStep = steps.find(s => s.type === 'result');
    const prompt = (item && item.prompt) || '';
    if (!prompt && !steps.length) return [];
    return [{
        prompt: prompt,
        steps: steps,
        result: resultStep ? resultStep.result : ''
    }];
}

function replayTurn(turn) {
    addMessage(turn.prompt || '', 'user');
    resetProcessState();
    const steps = turn.steps || [];
    steps.forEach(step => {
        if (step.type === 'result') {
            return;
        }
        ensureThoughtBlock(false);
        if (step.type === 'log' && step.result && step.result.indexOf('Executing step') > -1) {
            processStepCount++;
        }
        appendThoughtLine(step.result);
    });
    const thinkSteps = steps.filter(s => s.type === 'think');
    let lastThink = '';
    for (let i = thinkSteps.length - 1; i >= 0; i--) {
        const text = stripThoughtPrefix(thinkSteps[i].result);
        if (text && !isInternalProcessText(text)) {
            lastThink = text;
            break;
        }
    }
    const resultText = turn.result || (steps.find(s => s.type === 'result') || {}).result || '';
    renderFinalAnswer(resultText, lastThink);
}

function replayTask(item) {
    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }
    currentTaskId = item.id;
    chatMessages.innerHTML = '';
    resetProcessState();
    getTurns(item).forEach(replayTurn);
    toggle_chat_state('none');
    renderSessionList(sessionCache);
}

function openSession(taskId) {
    fetch('/tasks/' + taskId)
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => {
                    throw new Error(err.detail || 'Session not found');
                });
            }
            return response.json();
        })
        .then(item => {
            const idx = sessionCache.findIndex(task => task.id === taskId);
            if (idx >= 0) {
                sessionCache[idx] = Object.assign({}, sessionCache[idx], item);
            }
            replayTask(item);
        })
        .catch(error => {
            showErrorToast(error.message);
        });
}

function startNewChat() {
    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }
    currentTaskId = null;
    chatMessages.innerHTML = '';
    resetProcessState();
    toggle_chat_state('none');
    renderSessionList(sessionCache);
    const input = document.getElementById('messageInput');
    if (input) input.focus();
}

function refreshSessionList() {
    fetch('/tasks')
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load sessions');
            }
            return response.json();
        })
        .then(tasks => {
            renderSessionList(tasks);
        })
        .catch(error => {
            console.error('Failed to load sessions:', error);
        });
}

function renderSessionList(tasks) {
    const list = document.getElementById('sessionList');
    if (!list) return;
    sessionCache = Array.isArray(tasks) ? tasks.slice() : [];
    sessionCache.sort((a, b) => sessionUpdatedAt(b) - sessionUpdatedAt(a));
    list.innerHTML = '';

    if (sessionCache.length === 0) {
        const empty = document.createElement('li');
        empty.className = 'session-empty';
        empty.textContent = '暂无对话';
        list.appendChild(empty);
        return;
    }

    sessionCache.forEach(item => {
        const li = document.createElement('li');
        li.className = 'session-item' + (item.id === currentTaskId ? ' active' : '');
        li.dataset.taskId = item.id;

        const main = document.createElement('div');
        main.className = 'session-item-main';
        const titleEl = document.createElement('span');
        titleEl.className = 'session-title';
        titleEl.textContent = sessionTitle(item);
        main.appendChild(titleEl);

        const actions = document.createElement('div');
        actions.className = 'session-actions';

        const moreBtn = document.createElement('button');
        moreBtn.type = 'button';
        moreBtn.className = 'session-more-btn';
        moreBtn.title = '更多';
        moreBtn.setAttribute('aria-label', '更多');
        moreBtn.textContent = '···';

        const menu = document.createElement('div');
        menu.className = 'session-menu';

        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'session-menu-item';
        renameBtn.textContent = '重命名';
        renameBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            closeSessionMenus();
            beginRenameSession(item, titleEl);
        });

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'session-menu-item session-menu-item-danger';
        deleteBtn.textContent = '删除';
        deleteBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            closeSessionMenus();
            deleteSession(item.id);
        });

        menu.appendChild(renameBtn);
        menu.appendChild(deleteBtn);
        moreBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            const willOpen = !actions.classList.contains('open');
            closeSessionMenus();
            if (willOpen) {
                actions.classList.add('open');
                li.classList.add('menu-open');
            }
        });

        actions.appendChild(moreBtn);
        actions.appendChild(menu);
        li.appendChild(main);
        li.appendChild(actions);
        li.addEventListener('click', function () {
            openSession(item.id);
        });
        list.appendChild(li);
    });
}

function closeSessionMenus() {
    document.querySelectorAll('.session-actions.open').forEach(function (el) {
        el.classList.remove('open');
    });
    document.querySelectorAll('.session-item.menu-open').forEach(function (el) {
        el.classList.remove('menu-open');
    });
}

function beginRenameSession(task, titleEl) {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'session-rename-input';
    input.value = sessionTitle(task);
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let submitted = false;
    const finish = function (save) {
        if (submitted) return;
        submitted = true;
        if (!save) {
            refreshSessionList();
            return;
        }
        renameSession(task.id, input.value);
    };
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            finish(true);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            finish(false);
        }
    });
    input.addEventListener('blur', function () {
        finish(true);
    });
}

function renameSession(taskId, title) {
    const trimmed = (title || '').trim();
    if (!trimmed) {
        refreshSessionList();
        return;
    }
    fetch('/tasks/' + taskId, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed })
    })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => { throw new Error(err.detail || 'Rename failed'); });
            }
            return response.json();
        })
        .then(updated => {
            const idx = sessionCache.findIndex(item => item.id === taskId);
            if (idx >= 0) {
                sessionCache[idx] = Object.assign({}, sessionCache[idx], updated);
            }
            renderSessionList(sessionCache);
        })
        .catch(error => {
            showErrorToast(error.message);
            refreshSessionList();
        });
}

function deleteSession(taskId) {
    if (!window.confirm('确定删除该对话？')) return;
    fetch('/tasks/' + taskId, { method: 'DELETE' })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => { throw new Error(err.detail || 'Delete failed'); });
            }
            if (currentTaskId === taskId) {
                startNewChat();
            }
            refreshSessionList();
        })
        .catch(error => {
            showErrorToast(error.message);
        });
}

function applyHistory(tasks) {
    if (!tasks) return;
    const historyModal = new bootstrap.Modal(document.getElementById('historyModal'));
    const historyList = document.getElementById('historyList');

    historyList.innerHTML = '';

    if (tasks.length === 0) {
        historyList.innerHTML = '<li class="list-group-item text-muted">Record not found!</li>';
    } else {
        tasks.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        tasks.forEach(item => {
            const title = sessionTitle(item)
            const li = document.createElement('li');
            li.className = 'list-group-item d-flex justify-content-between align-items-center';
            const titleDiv = document.createElement('div');
            titleDiv.className = 'fw-bold';
            titleDiv.textContent = title;
            const timeDiv = document.createElement('small');
            timeDiv.className = 'text-muted';
            timeDiv.textContent = item.created_at ? new Date(item.created_at).toLocaleString() : '';
            li.appendChild(titleDiv);
            li.appendChild(timeDiv);

            li.addEventListener('click', function () {
                openSession(item.id);
                historyModal.hide();
            });

            historyList.appendChild(li);
        });
    }

    historyModal.show();
}

function addMessage(text, sender) {
    // 兜底：任何试图把工具日志写成 AI 气泡的调用，一律改写入过程块
    if (sender === 'ai' && isInternalProcessText(text)) {
        ensureThoughtBlock(false);
        appendThoughtLine(text);
        updateThoughtHeader(false);
        return;
    }
    const messageDiv = document.createElement('div');
    messageDiv.classList.add(sender + '-message');

    const iconDiv = document.createElement('div');
    iconDiv.className = 'message-icon';
    const icon = document.createElement('i');
    icon.className = sender === 'user' ? 'bi bi-person-fill' : 'bi bi-robot';
    iconDiv.appendChild(icon);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    if (sender === 'user') {
        contentDiv.innerHTML = `
        <div class="user-prompt">${text}</div>
        <button class="copy-btn" onclick="copyMessage(this)" title="Copy">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-copy"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>
        </button>
        `;
    } else {
        contentDiv.textContent = text;
    }

    if (sender === 'user') {
        messageDiv.appendChild(contentDiv);
        messageDiv.appendChild(iconDiv);
    } else {
        messageDiv.appendChild(iconDiv);
        messageDiv.appendChild(contentDiv);
    }

    chatMessages.appendChild(messageDiv);
    scrollView();
}

function scrollView() {
    if (!chatMessages) return
    chatMessages.scrollIntoView({ behavior: "auto", block: "end" })
}

// 创建（仅一次）本轮任务的过程块：一个 AI 消息内含 [折叠的执行过程块 + 最终结果区]
// expanded=true 时过程块默认展开（勾选 Thinking），否则默认折叠
function ensureThoughtBlock(expanded) {
    if (thoughtQuote) return;

    aiMessageDiv = document.createElement('div');
    aiMessageDiv.className = 'message ai-message';

    const iconDiv = document.createElement('div');
    iconDiv.className = 'message-icon';
    const icon = document.createElement('i');
    icon.className = 'bi bi-robot';
    iconDiv.appendChild(icon);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    thoughtQuote = document.createElement('div');
    thoughtQuote.className = 'thought-quote' + (expanded ? '' : ' collapsed');
    thoughtQuote.innerHTML = `
        <div class="quote-header">
            <span class="process-title">Thinking...</span>
            <span class="toggle-icon${expanded ? ' expanded' : ''}"><i class="bi ${expanded ? 'bi-chevron-down' : 'bi-chevron-up'}"></i></span>
        </div>
        <div class="quote-content"></div>
    `;

    finalAnswer = document.createElement('div');
    finalAnswer.className = 'ai-final-answer';

    contentDiv.appendChild(thoughtQuote);
    contentDiv.appendChild(finalAnswer);

    aiMessageDiv.appendChild(iconDiv);
    aiMessageDiv.appendChild(contentDiv);

    chatMessages.appendChild(aiMessageDiv);
    scrollView();

    thoughtQuote.addEventListener('click', function (e) {
        if (e.target.closest('.quote-header')) {
            const isCollapsing = !this.classList.contains('collapsed');
            this.classList.toggle('collapsed');
            const icon = this.querySelector('.toggle-icon');
            icon.innerHTML = isCollapsing ? '<i class="bi bi-chevron-up"></i>' : '<i class="bi bi-chevron-down"></i>';
        }
    });
}

// 向过程块追加一行过程记录（think/tool/act/log 内容）
function appendThoughtLine(text) {
    if (!thoughtQuote) return;
    const line = document.createElement('div');
    line.className = 'thinking-message';
    line.textContent = text;
    thoughtQuote.querySelector('.quote-content').appendChild(line);
}

// 更新过程块标题：进行中显示实时步数，完成后显示步数汇总
function updateThoughtHeader(done) {
    if (!thoughtQuote) return;
    const title = thoughtQuote.querySelector('.process-title');
    if (!title) return;
    title.textContent = done
        ? `Thought process (${processStepCount} step${processStepCount === 1 ? '' : 's'})`
        : `Thinking... (Step ${Math.max(processStepCount, 1)})`;
}

// 强制折叠过程块（任务结束时调用，图标同步切换）
function collapseThoughtBlock() {
    if (!thoughtQuote) return;
    thoughtQuote.classList.add('collapsed');
    const icon = thoughtQuote.querySelector('.toggle-icon');
    if (icon) {
        icon.classList.remove('expanded');
        icon.innerHTML = '<i class="bi bi-chevron-up"></i>';
    }
}

function getCustomCss() {
    return `
        <style>
            .message-content h1, .message-content h2, .message-content h3,
            .message-content h4, .message-content h5, .message-content h6 {
                margin: 10px 0;
                color: #343a40;
            }
            .message-content p {
                margin: 5px 0;
            }
            .message-content ul, .message-content ol {
                margin: 10px 0;
                padding-left: 20px;
            }
            .message-content li {
                margin: 5px 0;
            }
            .message-content a {
                color: #007bff;
                text-decoration: none;
            }
            .message-content a:hover {
                text-decoration: underline;
            }
            .message-content code {
                background-color: #f8f9fa;
                padding: 2px 4px;
                border-radius: 4px;
            }
            .message-content pre {
                background-color: #f8f9fa;
                padding: 10px;
                border-radius: 8px;
                overflow-x: auto;
            }
        </style>
    `;
}

document.addEventListener('DOMContentLoaded', function () {
    // Initialize tooltip
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.forEach(tooltipTriggerEl => {
        new bootstrap.Tooltip(tooltipTriggerEl);
    });

    document.getElementById('btn-paperclip').addEventListener('click', function() {
        document.getElementById('fileInput').click();
    });

    document.getElementById('fileInput').addEventListener('change', function(event) {
        const fileInput = event.target;
        const file = event.target.files[0];
        if (file) {
            if (file.type === 'text/plain') {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const content = e.target.result;
                    document.getElementById('messageInput').value = content;
                    fileInput.value = '';
                };
                reader.readAsText(file);
            } else {
                showErrorToast('Please select a text (.txt) file.');
                document.getElementById('fileInput').value = '';
                document.getElementById('messageInput').value = '';
                fileInput.value = '';
            }
        }
    });

    const messageInput = document.getElementById('messageInput');
    const sendButton = document.getElementById('sendButton');
    chatMessages = document.getElementById('chatMessages');

    if (!messageInput || !sendButton || !chatMessages) {
        console.error('Required elements not found!');
        return;
    }

    toggle_chat_state('none');

    const promptShortcuts = document.querySelectorAll('.prompt-shortcut');

    function sendMessage() {
        if (chat_state !== 'none') {
            showErrorToast('Chat bot still under working, please wait...');
            return;
        }
        const message = messageInput.value.trim();
        if (message) {
            toggle_chat_state('');
            createChat();
        }
    }

    sendButton.addEventListener('click', sendMessage);
    messageInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    promptShortcuts.forEach(shortcut => {
        shortcut.addEventListener('click', function () {
            messageInput.value = this.textContent;
            messageInput.focus();
        });
    });

    document.querySelector('.btn-chat').addEventListener('click', function () {
        startNewChat();
    });

    const btnNewSession = document.getElementById('btnNewSession');
    if (btnNewSession) {
        btnNewSession.addEventListener('click', startNewChat);
    }

    document.querySelector('.btn-history').addEventListener('click', function () {
        loadHistory();
    });

    refreshSessionList();

    document.addEventListener('click', function () {
        closeSessionMenus();
    });
});
