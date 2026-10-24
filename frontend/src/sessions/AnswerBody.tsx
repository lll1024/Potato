import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Parent, Root } from 'mdast';

function remarkChineseAutolinks() {
  return (tree: Root, file: {value: unknown}) => {
    const source = String(file.value);
    function splitAutolinks(parent: Parent) {
      for (let index = 0; index < parent.children.length; index++) {
        const node = parent.children[index];
        if (node.type === 'link' && node.children.length === 1 && node.children[0].type === 'text') {
          const label = node.children[0].value;
          const raw = source.slice(node.position?.start.offset, node.position?.end.offset);
          // 仅拆分 GFM 生成的裸网址；显式链接可包含中文标点。
          if (raw === label && /^(?:https?:\/\/|www\.)/i.test(raw)) {
            const boundary = label.search(/[，。；：！？、（）【】《》“”‘’]/u);
            if (boundary !== -1) {
              const suffix = label.slice(boundary);
              node.url = node.url.slice(0, node.url.length - suffix.length);
              node.children[0].value = label.slice(0, boundary);
              parent.children.splice(index + 1, 0, {type: 'text', value: suffix});
            }
          }
        } else if ('children' in node) {
          splitAutolinks(node);
        }
      }
    }
    splitAutolinks(tree);
  };
}

export function AnswerBody({text}: {text: string}) {
  return <div className="answer-body"><Markdown remarkPlugins={[remarkGfm, remarkChineseAutolinks]} components={{
    table: ({children}) => <div className="answer-table" role="region" aria-label="回答表格，可横向滚动" tabIndex={0}><table>{children}</table></div>,
    img: ({alt, src}) => <a href={src}>{alt || '图片链接'}</a>,
  }}>{text}</Markdown></div>;
}
