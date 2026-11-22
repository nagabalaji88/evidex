import { Component, type ReactNode } from "react";

/** Wraps the chat and upload panels, so one failure shows a message instead of a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <section className="card" role="alert">
          <h2>The {this.props.label} hit a problem</h2>
          <p className="callout bad" style={{ marginTop: 10 }}>{this.state.error.message}</p>
          <button type="button" className="btn" style={{ marginTop: 10 }} onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </section>
      );
    }
    return this.props.children;
  }
}
