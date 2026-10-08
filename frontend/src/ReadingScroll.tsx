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
  private attach = (element: HTMLDivElement | null) => {
    this.element=element;
    this.props.scrollRef(element);
  };
  getSnapshotBeforeUpdate(previous: Props): Anchor | null {
    if (!this.props.paused || previous.identity !== this.props.identity || !this.element) return null;
    const viewport=this.element.getBoundingClientRect();
    for (const item of this.element.querySelectorAll<HTMLElement>('[data-reading-anchor]')) {
      const bounds=item.getBoundingClientRect();
      if (bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom) {
        return {identity:item.dataset.readingAnchor!,offset:bounds.top-viewport.top};
      }
    }
    return null;
  }
  componentDidUpdate(_previous: Props, _state: object, anchor: Anchor | null) {
    if (!anchor || !this.props.paused || !this.element) return;
    const item=Array.from(this.element.querySelectorAll<HTMLElement>('[data-reading-anchor]'))
      .find(element => element.dataset.readingAnchor===anchor.identity);
    if (item) this.element.scrollTop += item.getBoundingClientRect().top-this.element.getBoundingClientRect().top-anchor.offset;
  }
  render() {
    return <div className={this.props.className} ref={this.attach} onScroll={this.props.onScroll} onClickCapture={this.props.onClickCapture}>{this.props.children}</div>;
  }
}
