import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function safeMarkdownUrl(value: string): string {
  const url = value.trim();
  if (/^(https?:\/\/|\/(?!\/)|#)/i.test(url)) return url;
  return "";
}
export function Markdown({
  content,
  headingIdPrefix,
}: {
  content: string;
  headingIdPrefix?: string;
}) {
  return (
    <article className="bank-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeMarkdownUrl}
        components={{
          ...(headingIdPrefix
            ? {
                h2: ({ node, children }) => (
                  <h2
                    id={`${headingIdPrefix}-${node?.position?.start.offset}`}
                    tabIndex={-1}
                  >
                    {children}
                  </h2>
                ),
              }
            : {}),
          img: () => null,
          a: ({ children, href }) =>
            href ? (
              <a href={href} rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
        }}
      >
        {content}
      </ReactMarkdown>
    </article>
  );
}
