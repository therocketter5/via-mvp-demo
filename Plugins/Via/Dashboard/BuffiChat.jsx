import { useEffect, useRef, useState } from 'react';
import Markdown from 'markdown-to-jsx';
import { runBrowserAgent } from '../../../frontend/src/services/agent';
import './BuffiChat.css';

// Small floating Buffi chat for the Via dashboard. Runs the in-browser agent
// with whatever tools the dashboard hands it — including update_dashboard, so
// answers can change what the charts display.
export default function BuffiChat({ systemPrompt, tools, suggestion }) {
  const [open, setOpen] = useState(true);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading, open]);

  // Abort any in-flight request when the dashboard unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const handleSend = async (e) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    const history = messages;
    setMessages((prev) => [...prev, { from: 'user', text: trimmed }]);
    setInput('');
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const reply = await runBrowserAgent({
        userMessage: trimmed,
        history,
        signal: controller.signal,
        systemPrompt,
        tools,
      });
      setMessages((prev) => [...prev, { from: 'bot', text: reply }]);
    } catch (err) {
      if (err?.name !== 'AbortError') {
        setMessages((prev) => [...prev, { from: 'bot', text: `Error: ${err.message}` }]);
      }
    }
    abortRef.current = null;
    setLoading(false);
  };

  const handleStop = () => abortRef.current?.abort();

  if (!open) {
    return (
      <button className="via-chat-fab" onClick={() => setOpen(true)}>
        Ask Buffi
      </button>
    );
  }

  return (
    <div className="via-chat-window">
      <div className="via-chat-header">
        <span className="via-chat-title">Ask Buffi</span>
        <button
          className="via-chat-minimize"
          onClick={() => setOpen(false)}
          aria-label="Minimize chat"
        >
          –
        </button>
      </div>

      <div className="via-chat-messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="via-chat-empty">
            Ask about the data — or change the view, e.g. &ldquo;{suggestion}&rdquo;
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`via-chat-msg via-chat-msg--${m.from}`}>
            {m.from === 'bot' ? <Markdown>{m.text}</Markdown> : m.text}
          </div>
        ))}
        {loading && <div className="via-chat-msg via-chat-msg--bot">Thinking…</div>}
      </div>

      <form className="via-chat-inputrow" onSubmit={handleSend}>
        <input
          className="via-chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask Buffi about VIA…"
        />
        {loading ? (
          <button type="button" className="via-chat-send" onClick={handleStop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="via-chat-send" disabled={!input.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}
