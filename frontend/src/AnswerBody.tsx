import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function AnswerBody({text}: {text: string}) {
  return <div className="answer-body"><Markdown remarkPlugins={[remarkGfm]} components={{
    table: ({children}) => <div className="answer-table" role="region" aria-label="回答表格，可横向滚动" tabIndex={0}><table>{children}</table></div>,
    img: ({alt, src}) => <a href={src}>{alt || '图片链接'}</a>,
  }}>{text}</Markdown></div>;
}
