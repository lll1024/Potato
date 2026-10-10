type Name = 'panel' | 'plus' | 'send' | 'close' | 'compass' | 'chat' | 'trace';

const paths: Record<Name, string> = {
  panel: 'M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm4 0v18',
  plus: 'M12 5v14M5 12h14',
  send: 'M12 19V5M5 12l7-7 7 7',
  close: 'm6 6 12 12M6 18 18 6',
  compass: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-6-3-2 4-4 2 2-4 4-2Z',
  chat: 'M21 11a8 8 0 0 1-8 8H5l-3 3V11a8 8 0 0 1 8-8h3a8 8 0 0 1 8 8Z',
  trace: 'M4 6h4m4 0h8M4 12h8m4 0h4M4 18h4m4 0h8',
};

export function Icon({name, size = 18}: {name: Name; size?: number}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
