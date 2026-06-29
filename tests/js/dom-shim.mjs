class TestElement {
  constructor(tagName = "element") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.eventListeners = {};
    this.textContent = "";
    this.innerHTML = "";
    this.disabled = false;
  }

  append(...children) {
    this.children.push(...children);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith("data-")) {
      const key = name
        .slice(5)
        .replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
  }

  getAttribute(name) {
    return this.attributes[name];
  }

  addEventListener(type, handler) {
    this.eventListeners[type] = handler;
  }

  click() {
    if (this.eventListeners.click) {
      this.eventListeners.click(new Event("click"));
    }
  }

  querySelector(selector) {
    if (!selector.startsWith("[data-action=\"")) {
      return null;
    }
    const action = selector.slice(14, -2);
    return this._find((child) => child.dataset?.action === action);
  }

  _find(predicate) {
    for (const child of this.children) {
      if (predicate(child)) {
        return child;
      }
      if (typeof child._find === "function") {
        const found = child._find(predicate);
        if (found) {
          return found;
        }
      }
    }
    return null;
  }
}

class TestShadowRoot extends TestElement {
  constructor() {
    super("shadow-root");
  }
}

class TestHTMLElement extends TestElement {
  constructor() {
    super("custom-element");
    this.shadowRoot = null;
  }

  attachShadow() {
    this.shadowRoot = new TestShadowRoot();
    return this.shadowRoot;
  }
}

const registry = new Map();

globalThis.HTMLElement = TestHTMLElement;
globalThis.customElements = {
  define(name, klass) {
    registry.set(name, klass);
  },
  get(name) {
    return registry.get(name);
  },
};

globalThis.document = {
  createElement(tagName) {
    return new TestElement(tagName);
  },
};

globalThis.window = globalThis;

globalThis.Event = class Event {
  constructor(type) {
    this.type = type;
  }
};
