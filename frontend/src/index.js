import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { CsvProvider } from './context/CsvContext';

// GitHub Pages can't serve an SPA fallback for deep links, so Pages builds
// use hash-based routing (set via REACT_APP_ROUTER=hash at build time).
const Router = process.env.REACT_APP_ROUTER === 'hash' ? HashRouter : BrowserRouter;

const root = ReactDOM.createRoot(document.getElementById('root'));

root.render(
  <React.StrictMode>
    <Router>
      <AuthProvider>
        <CsvProvider>
          <App />
        </CsvProvider>
      </AuthProvider>
    </Router>
  </React.StrictMode>
);

reportWebVitals();
