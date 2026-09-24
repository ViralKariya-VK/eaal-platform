// Editor abstraction: Monaco when its loader is available, a plain
// <textarea> otherwise. Mirrors the old Python EditorWidget split
// (MonacoEditor / FallbackEditor) — the rest of the app only ever calls
// getValue()/setValue()/onChange()/dispose(), never knowing which backend
// it got, so the app still runs even if `scripts/fetch_monaco.py` was
// never run (no npm install required to use the app).

function monacoLoaderAvailable() {
  return typeof require !== "undefined" && typeof require.config === "function";
}

// Creates an editor inside `container` and resolves with the
// {getValue, setValue, onChange, dispose} interface once ready.
function createEditor(container, initialValue) {
  if (monacoLoaderAvailable()) {
    return createMonacoEditor(container, initialValue);
  }
  return Promise.resolve(createFallbackEditor(container, initialValue));
}

function createMonacoEditor(container, initialValue) {
  return new Promise((resolve) => {
    require.config({ paths: { vs: "../assets/monaco/vs" } });
    require(["vs/editor/editor.main"], function () {
      const editor = monaco.editor.create(container, {
        value: initialValue || "",
        language: "python",
        theme: "vs-dark",
        automaticLayout: true,
        minimap: { enabled: false },
        fontSize: 13,
        fontFamily: "JetBrains Mono, monospace",
      });
      let changeCallback = null;
      let cursorCallback = null;
      editor.onDidChangeModelContent(() => {
        if (changeCallback) changeCallback(editor.getValue());
      });
      editor.onDidChangeCursorPosition((e) => {
        if (cursorCallback) cursorCallback(e.position.lineNumber, e.position.column);
      });
      resolve({
        getValue: () => editor.getValue(),
        setValue: (v) => editor.setValue(v),
        onChange: (cb) => {
          changeCallback = cb;
        },
        onCursorMove: (cb) => {
          cursorCallback = cb;
        },
        dispose: () => editor.dispose(),
      });
    });
  });
}

function createFallbackEditor(container, initialValue) {
  container.classList.add("fallback");
  const textarea = document.createElement("textarea");
  textarea.value = initialValue || "";
  textarea.spellcheck = false;
  container.appendChild(textarea);

  let changeCallback = null;
  textarea.addEventListener("input", () => {
    if (changeCallback) changeCallback(textarea.value);
  });

  return {
    getValue: () => textarea.value,
    setValue: (v) => {
      textarea.value = v;
    },
    onChange: (cb) => {
      changeCallback = cb;
    },
    onCursorMove: () => {
      // The plain-textarea fallback doesn't track cursor position — the
      // workspace status bar shows a placeholder instead.
    },
    dispose: () => {
      container.classList.remove("fallback");
      container.removeChild(textarea);
    },
  };
}
