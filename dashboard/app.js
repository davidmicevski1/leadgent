const STATUS_LABELS = {
  todo: "To Do",
  in_progress: "In Progress",
  done: "Done",
};

const PRIORITY_ORDER = {
  high: 0,
  medium: 1,
  low: 2,
};

const VIEW_IDS = ["tasks-view", "docs-view"];
const MODAL_IDS = ["add-task-modal", "task-modal"];

const state = {
  tasks: [],
  selectedTaskId: null,
  draggingTaskId: null,
  documents: [],
  selectedDocPath: null,
  activeView: "tasks-view",
  currentUser: null,
};

const $ = (id) => document.getElementById(id);

const apiRequest = async (url, options = {}) => {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }

  return payload;
};

const escapeHtml = (value) =>
  String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");

const parseTags = (raw) =>
  String(raw || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

const prettyDate = (iso) => {
  if (!iso) return "";
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return iso;
  return value.toLocaleString();
};

const sortTasks = (tasks) =>
  [...tasks].sort((a, b) => {
    const p = (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99);
    if (p !== 0) return p;
    return (a.title || "").localeCompare(b.title || "");
  });

const getSelectedTask = () => state.tasks.find((task) => task.id === state.selectedTaskId) || null;

const setView = (viewId) => {
  state.activeView = viewId;

  VIEW_IDS.forEach((id) => {
    const node = $(id);
    if (node) node.classList.toggle("hidden", id !== viewId);
  });

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("is-active", button.getAttribute("data-view") === viewId);
  });
};

const openModal = (modalId) => {
  const modal = $(modalId);
  if (!modal) return;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
};

const closeModal = (modalId) => {
  const modal = $(modalId);
  if (!modal) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");

  const hasOpenModal = MODAL_IDS.some((id) => {
    const node = $(id);
    return node && !node.classList.contains("hidden");
  });

  if (!hasOpenModal) {
    document.body.classList.remove("modal-open");
  }
};

const clearDropHighlights = () => {
  document.querySelectorAll(".column.drop-active").forEach((node) => node.classList.remove("drop-active"));
};

const updateCurrentUserUI = () => {
  const node = $("current-user");
  if (!node) return;
  node.textContent = state.currentUser ? `Signed in: ${state.currentUser}` : "Not signed in";
};

const renderTaskBoard = () => {
  const board = $("task-board");
  const statuses = ["todo", "in_progress", "done"];

  board.innerHTML = statuses
    .map((status) => {
      const tasks = sortTasks(state.tasks.filter((task) => task.status === status));
      const cards = tasks
        .map((task) => {
          const tags = (task.tags || [])
            .map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`)
            .join("");

          return `
            <article class="task-card" draggable="true" data-task-id="${escapeHtml(task.id)}">
              <p class="task-title">${escapeHtml(task.title)}</p>
              <p class="task-description">${escapeHtml(task.description || "No description")}</p>
              <div class="meta-row">
                <span class="priority ${escapeHtml(task.priority)}">${escapeHtml(task.priority)}</span>
                <span>${escapeHtml(task.dueDate || "No due date")}</span>
              </div>
              <div class="tag-row">${tags || '<span class="tag">no-tags</span>'}</div>
              <button type="button" class="btn" data-open-task="${escapeHtml(task.id)}">Open</button>
            </article>
          `;
        })
        .join("");

      return `
        <section class="column" data-drop-status="${status}">
          <div class="column-header">
            <h3>${STATUS_LABELS[status]}</h3>
            <span class="count-badge">${tasks.length}</span>
          </div>
          ${cards || '<p class="task-description">No tasks.</p>'}
        </section>
      `;
    })
    .join("");

  $("task-count").textContent = String(state.tasks.length);
};

const renderTaskModal = () => {
  const task = getSelectedTask();
  if (!task) return;

  $("task-modal-title").textContent = task.title || "Task";
  $("edit-title").value = task.title || "";
  $("edit-description").value = task.description || "";
  $("edit-status").value = task.status || "todo";
  $("edit-priority").value = task.priority || "medium";
  $("edit-due-date").value = task.dueDate || "";
  $("edit-tags").value = (task.tags || []).join(", ");

  const notes = $("task-notes");
  notes.innerHTML = (task.notes || [])
    .slice()
    .reverse()
    .map(
      (note) => `
      <li class="note-item">
        <div>${escapeHtml(note.text)}</div>
        <p class="note-time">${escapeHtml(prettyDate(note.createdAt))}</p>
      </li>
    `
    )
    .join("");

  if (!(task.notes || []).length) {
    notes.innerHTML = '<li class="note-item"><div>No notes yet.</div></li>';
  }
};

const renderDocuments = () => {
  const select = $("doc-select");
  const docs = [...state.documents].sort((a, b) => a.path.localeCompare(b.path));

  select.innerHTML = docs
    .map(
      (doc) =>
        `<option value="${escapeHtml(doc.path)}">${escapeHtml(doc.category)} / ${escapeHtml(doc.name)}</option>`
    )
    .join("");

  $("doc-count").textContent = String(docs.length);

  if (!docs.length) {
    state.selectedDocPath = null;
    $("doc-editor").value = "";
    setDocStatus("No markdown docs found in docs/ or templates/.", "error");
    return;
  }

  if (!state.selectedDocPath || !docs.some((doc) => doc.path === state.selectedDocPath)) {
    state.selectedDocPath = docs[0].path;
  }

  select.value = state.selectedDocPath;
};

const setDocStatus = (text, mode = "") => {
  const node = $("doc-status");
  node.textContent = text;
  node.classList.remove("success", "error");
  if (mode) node.classList.add(mode);
};

const loadCurrentUser = async () => {
  const payload = await apiRequest("/api/me");
  state.currentUser = payload?.user?.username || null;
  updateCurrentUserUI();
};

const loadTasks = async (selectTaskId = null) => {
  const payload = await apiRequest("/api/tasks");
  state.tasks = Array.isArray(payload.tasks) ? payload.tasks : [];

  if (selectTaskId) {
    state.selectedTaskId = selectTaskId;
  } else if (state.selectedTaskId && !state.tasks.some((task) => task.id === state.selectedTaskId)) {
    state.selectedTaskId = null;
  }

  renderTaskBoard();
  renderTaskModal();
};

const loadDocuments = async () => {
  const payload = await apiRequest("/api/docs");
  state.documents = Array.isArray(payload.documents) ? payload.documents : [];
  renderDocuments();

  if (state.selectedDocPath) {
    await loadDocumentContent(state.selectedDocPath);
  }
};

const loadDocumentContent = async (path) => {
  if (!path) return;
  const payload = await apiRequest(`/api/doc?path=${encodeURIComponent(path)}`);
  $("doc-editor").value = payload.content || "";
  state.selectedDocPath = payload.path;
  setDocStatus(`Editing ${payload.path}`, "");
};

const openAddTaskModal = () => {
  $("new-task-form").reset();
  $("new-status").value = "todo";
  $("new-priority").value = "medium";
  openModal("add-task-modal");
};

const moveTaskToStatus = async (taskId, newStatus) => {
  if (!taskId || !STATUS_LABELS[newStatus]) return;

  const task = state.tasks.find((item) => item.id === taskId);
  if (!task) return;
  if (task.status === newStatus) return;

  const payload = {
    title: task.title,
    description: task.description,
    status: newStatus,
    priority: task.priority,
    dueDate: task.dueDate,
    tags: task.tags || [],
  };

  await apiRequest(`/api/tasks/${encodeURIComponent(task.id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

  await loadTasks(task.id);
};

const handleNewTaskSubmit = async (event) => {
  event.preventDefault();

  const payload = {
    title: $("new-title").value,
    description: $("new-description").value,
    status: $("new-status").value,
    priority: $("new-priority").value,
    dueDate: $("new-due-date").value,
    tags: parseTags($("new-tags").value),
  };

  const response = await apiRequest("/api/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  closeModal("add-task-modal");
  await loadTasks(response.task.id);
  openModal("task-modal");
};

const handleEditTaskSubmit = async (event) => {
  event.preventDefault();
  const task = getSelectedTask();
  if (!task) return;

  const payload = {
    title: $("edit-title").value,
    description: $("edit-description").value,
    status: $("edit-status").value,
    priority: $("edit-priority").value,
    dueDate: $("edit-due-date").value,
    tags: parseTags($("edit-tags").value),
  };

  await apiRequest(`/api/tasks/${encodeURIComponent(task.id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

  await loadTasks(task.id);
  closeModal("task-modal");
};

const handleDeleteTask = async () => {
  const task = getSelectedTask();
  if (!task) return;

  const confirmed = window.confirm(`Delete task "${task.title}"? This cannot be undone.`);
  if (!confirmed) return;

  await apiRequest(`/api/tasks/${encodeURIComponent(task.id)}`, {
    method: "DELETE",
  });

  state.selectedTaskId = null;
  closeModal("task-modal");
  await loadTasks();
};

const handleAddNoteSubmit = async (event) => {
  event.preventDefault();
  const task = getSelectedTask();
  if (!task) return;

  const text = $("new-note-text").value.trim();
  if (!text) return;

  await apiRequest(`/api/tasks/${encodeURIComponent(task.id)}/notes`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });

  $("new-note-text").value = "";
  await loadTasks(task.id);
};

const handleBoardClick = (event) => {
  const button = event.target.closest("button[data-open-task]");
  if (!button) return;

  const taskId = button.getAttribute("data-open-task");
  state.selectedTaskId = taskId;
  renderTaskModal();
  openModal("task-modal");
};

const handleDragStart = (event) => {
  const card = event.target.closest(".task-card[draggable='true']");
  if (!card) return;

  const taskId = card.getAttribute("data-task-id");
  state.draggingTaskId = taskId;
  card.classList.add("dragging");

  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", taskId || "");
  }
};

const handleDragEnd = (event) => {
  const card = event.target.closest(".task-card[draggable='true']");
  if (card) card.classList.remove("dragging");
  state.draggingTaskId = null;
  clearDropHighlights();
};

const handleDragOver = (event) => {
  const zone = event.target.closest(".column[data-drop-status]");
  if (!zone) return;
  event.preventDefault();
  clearDropHighlights();
  zone.classList.add("drop-active");
};

const handleDrop = (event) => {
  const zone = event.target.closest(".column[data-drop-status]");
  if (!zone) return;

  event.preventDefault();
  const newStatus = zone.getAttribute("data-drop-status");
  const taskId =
    state.draggingTaskId || (event.dataTransfer ? event.dataTransfer.getData("text/plain") : null) || null;

  clearDropHighlights();
  state.draggingTaskId = null;

  moveTaskToStatus(taskId, newStatus).catch((error) => alert(error.message));
};

const handleDocSelectionChange = async (event) => {
  const path = event.target.value;
  await loadDocumentContent(path);
};

const handleSaveDoc = async () => {
  if (!state.selectedDocPath) {
    setDocStatus("No document selected.", "error");
    return;
  }

  const content = $("doc-editor").value;

  await apiRequest(`/api/doc?path=${encodeURIComponent(state.selectedDocPath)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });

  setDocStatus(`Saved ${state.selectedDocPath} at ${new Date().toLocaleTimeString()}`, "success");
};

const handleLogout = async () => {
  await apiRequest("/api/logout", {
    method: "POST",
    body: JSON.stringify({}),
  });
  window.location.href = "/login";
};

const wireEvents = () => {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.getAttribute("data-view")));
  });

  $("open-add-task-modal").addEventListener("click", openAddTaskModal);

  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => closeModal(button.getAttribute("data-close-modal")));
  });

  MODAL_IDS.forEach((modalId) => {
    const modal = $(modalId);
    if (!modal) return;
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeModal(modalId);
      }
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    MODAL_IDS.forEach((modalId) => closeModal(modalId));
  });

  $("new-task-form").addEventListener("submit", (event) => {
    handleNewTaskSubmit(event).catch((error) => alert(error.message));
  });

  $("edit-task-form").addEventListener("submit", (event) => {
    handleEditTaskSubmit(event).catch((error) => alert(error.message));
  });

  $("delete-task-button").addEventListener("click", () => {
    handleDeleteTask().catch((error) => alert(error.message));
  });

  $("add-note-form").addEventListener("submit", (event) => {
    handleAddNoteSubmit(event).catch((error) => alert(error.message));
  });

  $("task-board").addEventListener("click", handleBoardClick);
  $("task-board").addEventListener("dragstart", handleDragStart);
  $("task-board").addEventListener("dragend", handleDragEnd);
  $("task-board").addEventListener("dragover", handleDragOver);
  $("task-board").addEventListener("drop", handleDrop);

  $("doc-select").addEventListener("change", (event) => {
    handleDocSelectionChange(event).catch((error) => setDocStatus(error.message, "error"));
  });

  $("save-doc-button").addEventListener("click", () => {
    handleSaveDoc().catch((error) => setDocStatus(error.message, "error"));
  });

  $("logout-button").addEventListener("click", () => {
    handleLogout().catch((error) => alert(error.message));
  });
};

const init = async () => {
  wireEvents();
  setView("tasks-view");
  await loadCurrentUser();
  await loadTasks();
  await loadDocuments();
};

init().catch((error) => {
  alert(`Dashboard failed to load: ${error.message}`);
});
