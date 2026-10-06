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
        tabSize: 4,
        insertSpaces: true,
        detectIndentation: false,
        tabFocusMode: false,
      });
      // Tab must always indent inside the editor and never hand focus to the
      // next button on the page (some embedded web views let it). Handle it
      // here, before anything else sees the key. Tab still accepts a
      // suggestion when the suggestion list is open.
      container.addEventListener(
        "keydown",
        (e) => {
          if (e.key !== "Tab" || e.ctrlKey || e.altKey || e.metaKey) return;
          if (!e.target.classList || !e.target.classList.contains("inputarea")) return;
          if (container.querySelector(".suggest-widget.visible")) return;
          e.preventDefault();
          e.stopPropagation();
          editor.trigger("cavy", e.shiftKey ? "outdent" : "tab", null);
        },
        true
      );
      let changeCallback = null;
      let cursorCallback = null;
      let pasteCallback = null;
      let lastPasteAt = 0;
      editor.onDidChangeModelContent((e) => {
        if (changeCallback) changeCallback(editor.getValue());
        // Big insertions that are neither typing nor a paste (e.g. text dragged in
        // from another window) are reported too. Our own setValue() is a "flush".
        const big = e.changes.find((c) => c.text.length >= 30);
        if (big && !e.isFlush && !e.isUndoing && !e.isRedoing) {
          setTimeout(() => {
            if (pasteCallback && Date.now() - lastPasteAt > 300) pasteCallback(big.text, "drop");
          }, 80);
        }
      });
      editor.onDidPaste((e) => {
        lastPasteAt = Date.now();
        if (pasteCallback) pasteCallback(editor.getModel().getValueInRange(e.range), "paste");
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
        // Called with (text, how) when text is pasted or dropped into the editor.
        onPaste: (cb) => {
          pasteCallback = cb;
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
  textarea.addEventListener("keydown", (e) => handleFallbackKey(textarea, e));
  let pasteCallback = null;
  textarea.addEventListener("paste", (e) => {
    const text = e.clipboardData ? e.clipboardData.getData("text") : "";
    if (pasteCallback && text) pasteCallback(text, "paste");
  });
  textarea.addEventListener("drop", (e) => {
    const text = e.dataTransfer ? e.dataTransfer.getData("text") : "";
    if (pasteCallback && text) pasteCallback(text, "drop");
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
    onPaste: (cb) => {
      pasteCallback = cb;
    },
    dispose: () => {
      container.classList.remove("fallback");
      container.removeChild(textarea);
    },
  };
}

// -- Plain-text fallback: Tab indents (4 spaces), Shift+Tab outdents, Enter keeps the indent -----

const INDENT = "    ";

// Replace text[start:end] with `text`, through the browser's edit stack so Undo still works.
function replaceText(textarea, start, end, text) {
  textarea.focus();
  textarea.setSelectionRange(start, end);
  if (!document.execCommand || !document.execCommand("insertText", false, text)) {
    textarea.setRangeText(text, start, end, "end");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

function handleFallbackKey(textarea, e) {
  if (e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return;
  if (e.key === "Tab") {
    e.preventDefault();
    indentSelection(textarea, e.shiftKey);
  } else if (e.key === "Enter" && !e.shiftKey) {
    const { selectionStart: start, selectionEnd: end, value } = textarea;
    if (start !== end) return;
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    const before = value.slice(lineStart, start);
    const lead = /^[ \t]*/.exec(before)[0];
    const extra = /:\s*$/.test(before) ? INDENT : "";
    if (!lead && !extra) return;
    e.preventDefault();
    replaceText(textarea, start, end, "\n" + lead + extra);
  }
}

function indentSelection(textarea, outdent) {
  const { selectionStart: start, selectionEnd: end, value } = textarea;
  const multiLine = value.slice(start, end).includes("\n");

  if (!outdent && !multiLine) {
    // A plain Tab: move to the next 4-column stop.
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    const pad = INDENT.length - ((start - lineStart) % INDENT.length);
    replaceText(textarea, start, end, " ".repeat(pad));
    return;
  }

  // Work on whole lines (not counting a last line the selection only touches at its start).
  const blockStart = value.lastIndexOf("\n", start - 1) + 1;
  let blockEnd = end > start && value[end - 1] === "\n" ? end - 1 : end;
  const nextBreak = value.indexOf("\n", blockEnd);
  blockEnd = nextBreak === -1 ? value.length : nextBreak;
  const lines = value.slice(blockStart, blockEnd).split("\n");
  const changed = lines.map((line) => {
    if (!outdent) return line.length ? INDENT + line : line;
    return line.replace(/^( {1,4}|\t)/, "");
  });
  const replacement = changed.join("\n");
  replaceText(textarea, blockStart, blockEnd, replacement);

  // Keep the selection on the same text.
  const firstShift = changed[0].length - lines[0].length;
  const totalShift = replacement.length - (blockEnd - blockStart);
  if (start === end) {
    const cursor = Math.max(blockStart, start + firstShift);
    textarea.setSelectionRange(cursor, cursor);
  } else {
    textarea.setSelectionRange(Math.max(blockStart, start + firstShift), end + totalShift);
  }
}
