import { createRoot } from 'react-dom/client';
import { SessionsApp } from './sessions';
import { PreferencesView } from './preferences';
import './style.css';

createRoot(document.getElementById('root')!).render(<SessionsApp PreferencesView={PreferencesView} />);
