import { Component, type MouseEventHandler, type ReactNode, type RefCallback, type UIEventHandler } from 'react';

type Props = {
  identity: string | null; paused: boolean; className: string; children: ReactNode;
  scrollRef: RefCallback<HTMLDivElement>; onScroll: UIEventHandler<HTMLDivElement>;
  onClickCapture?: MouseEventHandler<HTMLDivElement>;
};
type Anchor = {identity: string; offset: number};

// 在 React 改写 DOM 前后保留可见内容位置；仅保持 scrollTop 无法抵消上方新增内容。
export class ReadingScroll extends Component<Props, object, Anchor | null> {
  private element: HTMLDivElement | null = null;
  private anchor: Anchor | null = null;
  private frame = 0;
  private layoutChanging = false;
  private userScrolling = false;
  private resize?: ResizeObserver;
  private mutations?: MutationObserver;
  private remember = () => {this.anchor = this.visibleAnchor();};
  private visibleAnchor(): Anchor | null {
    if (!this.element) return null;
    const viewport = this.element.getBoundingClientRect();
    for (const item of this.element.querySelectorAll<HTMLElement>('[data-reading-anchor]')) {
      const bounds = item.getBoundingClientRect();
      if (bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom) {
        return {identity: item.dataset.readingAnchor!, offset: bounds.top - viewport.top};
      }
    }
    return null;
  }
  private restore(anchor: Anchor | null) {
    if (!this.element) return;
    if (!this.props.paused) this.element.scrollTop = this.element.scrollHeight;
    else if (anchor) {
      const item = Array.from(this.element.querySelectorAll<HTMLElement>('[data-reading-anchor]'))
        .find(element => element.dataset.readingAnchor === anchor.identity);
      if (item) this.element.scrollTop += item.getBoundingClientRect().top - this.element.getBoundingClientRect().top - anchor.offset;
    }
    this.remember();
  }
  componentDidMount() {
    const changed = () => {
      cancelAnimationFrame(this.frame);
      this.layoutChanging = true;
      this.frame = requestAnimationFrame(() => {
        this.restore(this.anchor);
        this.frame = requestAnimationFrame(() => {this.layoutChanging = false;});
      });
    };
    this.resize = new ResizeObserver(changed);
    const observe = () => {for (const child of this.element?.children ?? []) this.resize?.observe(child); changed();};
    this.mutations = new MutationObserver(observe);
    if (this.element) this.mutations.observe(this.element, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['open']});
    observe();
  }
  componentWillUnmount() {cancelAnimationFrame(this.frame); this.resize?.disconnect(); this.mutations?.disconnect();}

  private attach = (element: HTMLDivElement | null) => {
    this.element=element;
    this.props.scrollRef(element);
  };
  getSnapshotBeforeUpdate(previous: Props): Anchor | null {
    if (!this.props.paused || previous.identity !== this.props.identity || !this.element) return null;
    return this.visibleAnchor();
  }
  componentDidUpdate(previous: Props, _state: object, anchor: Anchor | null) {
    if (previous.paused && !this.props.paused) this.userScrolling = false;
    if (anchor) this.restore(anchor);
    else this.remember();
  }
  render() {
    return <div className={this.props.className} ref={this.attach} onWheel={event => {if (event.deltaY) this.userScrolling = true;}}
      onTouchMove={() => {this.userScrolling = true;}}
      onPointerDown={event => {if (event.target === this.element) this.userScrolling = true;}}
      onKeyDown={event => {if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) this.userScrolling = true;}}
      onScroll={event => {if (!this.layoutChanging || this.userScrolling) {this.remember(); this.props.onScroll(event);}}} onClickCapture={this.props.onClickCapture}>{this.props.children}</div>;
  }
}
