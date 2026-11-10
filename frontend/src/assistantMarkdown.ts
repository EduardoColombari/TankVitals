import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true }).disable('image')
export const renderAssistantMarkdown = (text: string): string => markdown.render(text)
