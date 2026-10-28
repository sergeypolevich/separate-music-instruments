const stateDescriptions = {
  queued: "В очереди",
  searching: "Ищем источник",
  downloading: "Загружаем аудио",
  downloaded: "Аудио загружено",
  separating: "Разделяем дорожки",
  completed: "Обработка завершена",
  failed: "При обработке возникла ошибка",
  cancelled: "Задание отменено",
};

const batchActiveStates = new Set(["queued", "processing"]);
const form = document.querySelector("#submit-form");
const inputField = document.querySelector("#inputs");
const modelOptions = document.querySelector("#model-options");
let selectedModel = "";
const formMessage = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const openFileButton = document.querySelector("#open-file");
const fileInput = document.querySelector("#file-input");
const dropOverlay = document.querySelector("#drop-overlay");
const searchOverlay = document.querySelector("#search-overlay");
const searchTrigger = document.querySelector("#search-trigger");
const projectTabs = document.querySelector("#project-tabs");
const projectEmpty = document.querySelector("#project-empty");
const projectDetail = document.querySelector("#project-detail");
const detailTitle = document.querySelector("#detail-title");
// At most two lines (CSS); a longer title fades out at the end of the second line instead of scrolling.
const markClippedTitle = () => { detailTitle.toggleAttribute("data-clipped", detailTitle.scrollHeight > detailTitle.clientHeight + 1); };
new ResizeObserver(markClippedTitle).observe(detailTitle);
const detailControls = document.querySelector("#detail-controls");  // the master volume, over the mixer column
const detailTransport = document.querySelector("#detail-transport");  // ▶ ▶4 ⏮, before the title
const detailTime = document.querySelector("#detail-time");
const detailError = document.querySelector("#detail-error");
const playbackPanel = document.querySelector("#playback-panel");
const detailSize = document.querySelector("#detail-size");
let selectedProjectBytes = { disk: 0, shared: 0 };
const deleteProjectButton = document.querySelector("#delete-project");
const confirmDialog = document.querySelector("#confirm-dialog");
const confirmError = document.querySelector("#confirm-error");
const confirmButton = document.querySelector("#confirm-action");
const updatesMessage = document.querySelector("#updates-message");
const releaseList = document.querySelector("#release-list");
const checkUpdatesButton = document.querySelector("#check-updates");
const themeButtons = document.querySelectorAll("[data-theme-choice]");
const accentHueField = document.querySelector("#accent-hue");
const themeColorMeta = document.querySelector('meta[name="theme-color"]');
const settingsOverlay = document.querySelector("#settings-overlay");
const helpOverlay = document.querySelector("#help-overlay");
const queueTrigger = document.querySelector("#queue-trigger");
const queuePopover = document.querySelector("#queue-popover");
const tabsTrigger = document.querySelector("#tabs-trigger");
const tabsPopover = document.querySelector("#tabs-popover");
let pollingTimer = null;
let activeTransport = null;
let activeMixer = null;
// Actions of the open project's player, used by the global hotkeys (null while none is ready).
let activeControls = null;
let activeAudioContext = null;
let playbackSetupId = 0;
let playbackKey = "";
// Position, play state, speed and metronome to restore when a project switches to upgraded stems.
let pendingResume = null;
let selectedProjectId = null;
let selectedProjectTitle = "";
let selectedJobId = null;

// Icons are drawn in the same 24px grid as the static SVG icons in index.html (lucide style, ISC). The stem icons
// are Delapouite's from game-icons.net (CC BY 3.0, see THIRD_PARTY.md), drawn in 512 units and scaled to the grid.
const transportIcons = {
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  start: '<path d="M6 5v14"/><path d="M18 5.5v13L8.5 12z" fill="currentColor" stroke="none"/>',
  metronome: '<path d="M9 3h6l4 18H5z"/><path d="M12 16l5-9"/><path d="M7 15h10"/>',
  countin: '<text x="6.5" y="17" font-size="14" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">4</text><path d="M12.5 6.5v11l8.5-5.5z" fill="currentColor" stroke="none"/>',
  vocals: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M388.938 29.47c-23.008 0-46.153 9.4-62.688 25.405 5.74 46.14 21.326 75.594 43.75 94.28 22.25 18.543 52.078 26.88 87.75 28.345 13.432-16.07 21.188-37.085 21.188-58 0-23.467-9.75-47.063-26.344-63.656C436 39.25 412.404 29.47 388.938 29.47zm-76.282 42.374c-8.808 14.244-13.75 30.986-13.75 47.656 0 23.467 9.782 47.063 26.375 63.656 16.595 16.594 40.19 26.375 63.658 26.375 18.678 0 37.44-6.196 52.687-17.093-31.55-3.2-59.626-12.46-81.875-31-23.277-19.397-39.553-48.64-47.094-89.593zm-27.78 67.72-64.47 83.78c2.898 19.6 10.458 35.1 22.094 46.187 11.692 11.142 27.714 18.118 48.594 19.626l79.312-65.28c-21.2-3.826-41.14-14.11-56.437-29.407-14.927-14.927-25.057-34.286-29.095-54.907zM300 201.468a8 8 0 0 1 .03 0 8 8 0 0 1 .533 0 8 8 0 0 1 5.875 13.374l-34.313 38.78a8.004 8.004 0 1 1-12-10.593l34.313-38.78a8 8 0 0 1 5.562-2.78zM207.594 240 103 375.906c3.487 13.327 7.326 20.944 12.5 26.03 5.03 4.948 12.386 8.46 23.563 12.408l135.312-111.438c-17.067-3.61-31.595-11.003-42.906-21.78-11.346-10.81-19.323-24.827-23.876-41.126zM95.97 402.375c-9.12 5.382-17.37 14.08-23.126 24.406-9.656 17.317-11.52 37.236-2.25 50.47 6.665 4.337 10.566 4.81 13.844 4.344 1.794-.256 3.618-.954 5.624-1.875-3.18-9.575-6.3-20.93-2.5-33.314 3.03-9.87 10.323-19.044 23.47-27.5a49.47 49.47 0 0 1-6.75-5.562c-3.217-3.163-5.94-6.78-8.313-10.97z"/></g>',
  drums: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M476.865 25.514 282.941 145.422l19.32 9.215L486.333 40.822l-9.467-15.308zM73.6 65.508l-7.748 16.246 286.595 136.709 1.389 2.715c3.244 6.346 9.677 12.524 17.723 16.373l.003.002.004.002c8.566 4.084 17.188 5.364 23.63 4.373 6.443-.992 10.352-3.63 12.523-8.18 2.17-4.55 1.665-8.39-1.725-13.603-3.39-5.213-10.122-10.882-18.935-15.086-8.063-3.843-16.927-4.961-23.91-3.487l-2.983.63L73.6 65.507zm121.097 97.65c-40.936 2.913-77.435 8.644-106.084 16.283-21.165 5.644-38.06 12.392-48.953 19.26C28.767 205.57 25 211.723 25 216c0 4.277 3.767 10.43 14.66 17.299 10.894 6.868 27.788 13.616 48.953 19.26C130.943 263.847 190.374 271 256 271c49.18 0 94.88-4.017 132.512-10.781-8.123-.325-16.546-2.53-24.703-6.422l-.002-.002-.004-.002c-10.2-4.878-18.614-12.38-24.135-21.484l-72.705-34.68-36.498 22.566c-4.5 9.65-12.051 18.021-21.662 23.977l-.002.002-.004.002c-10.715 6.625-22.297 10.072-33.281 9.588-10.985-.484-21.794-5.485-27.856-15.29-6.062-9.803-5.366-21.696-.515-31.294 4.85-9.598 13.3-17.76 23.785-24.243l.002-.001h.002c9.63-5.951 20.504-8.973 31.16-8.688l6.904-4.27-14.3-6.82zm127.432.36-.545.336 41.3 19.7c10.563-1.44 21.702.385 31.92 5.257h.005c11.125 5.306 20.412 12.504 26.275 21.52 5.863 9.014 7.844 20.763 2.88 31.167-3.08 6.457-8.178 11.27-14.2 14.395 4.699-1.07 9.273-2.174 13.623-3.334 21.165-5.644 38.06-12.392 48.953-19.26C483.233 226.43 487 220.277 487 216c0-4.277-3.767-10.43-14.66-17.299-10.894-6.868-27.788-13.616-48.953-19.26-27.516-7.337-62.286-12.91-101.258-15.923zm-93.81 15.677-21.499 13.293-3.033-.3c-7.102-.708-15.793 1.364-23.39 6.058l-.002.002c-8.305 5.135-14.382 11.502-17.186 17.05-2.804 5.55-2.89 9.422-.238 13.71 2.651 4.288 6.822 6.486 13.336 6.773 6.513.287 14.949-1.923 23.023-6.916 7.58-4.699 13.305-11.538 15.842-18.199l1.086-2.85 31.383-19.404-19.323-9.217zM39.642 253.912l26.941 44.9 33.31-24.982a408.545 408.545 0 0 1-15.92-3.879c-17.352-4.627-32.253-9.928-44.331-16.039zm432.714 0c-12.078 6.11-26.98 11.412-44.332 16.04-6.926 1.846-14.283 3.565-21.949 5.175l38.926 24.377 27.355-45.592zM487 264.492l-17.576 29.293a90.955 90.955 0 0 0 2.209-.97c5.948-2.72 10.255-5.583 12.627-7.944 2.371-2.361 2.74-3.683 2.74-4.871v-15.508zm-462 .002V280c0 1.188.369 2.51 2.74 4.871 2.372 2.361 6.679 5.224 12.627 7.943.712.326 1.45.65 2.207.971L25 264.495zm98.639 14.027-36.737 27.553c1.874.36 3.74.72 5.674 1.065 21.728 3.862 47.224 6.792 74.612 8.775l-36.143-36.143a573.54 573.54 0 0 1-7.406-1.25zm233.996 4.538c-23.345 2.877-48.603 4.787-75.155 5.552l37.08 27.809 38.075-33.361zm27.261.045-37.195 32.59c26.286-1.979 50.764-4.83 71.723-8.555 1.011-.18 1.972-.373 2.967-.557l-37.495-23.478zm-224.369.697 32.317 32.316 36.676-27.506c-24.226-.698-47.389-2.342-68.993-4.81zM256 291.25l-36.322 27.242c12.024.332 24.171.508 36.322.508 12.15 0 24.298-.176 36.322-.508L256 291.25zM25 305.121V440c0 5.5 4.3 11.699 16.09 18.436 3.953 2.259 8.63 4.452 13.91 6.554V317.13c-8.28-2.377-15.693-5.007-22.117-7.943-2.813-1.287-5.44-2.63-7.883-4.065zm462 0c-2.443 1.435-5.07 2.778-7.883 4.065-6.424 2.936-13.838 5.566-22.117 7.943V464.99c5.28-2.103 9.957-4.295 13.91-6.554C482.7 451.699 487 445.5 487 440V305.121zM73 321.63v149.488c6.196 1.816 12.868 3.523 19.953 5.098 25.716 5.714 56.789 9.79 90.047 12.2V334.978c-34.264-1.945-66.46-5.297-93.576-10.118A430.035 430.035 0 0 1 73 321.63zm366 0a430.032 430.032 0 0 1-16.424 3.232c-27.115 4.82-59.312 8.173-93.576 10.118v153.435c33.258-2.409 64.331-6.485 90.047-12.2 7.085-1.574 13.757-3.281 19.953-5.097V321.63zm-238 14.24v153.684a1038.65 1038.65 0 0 0 55 1.445c18.483 0 36.972-.485 55-1.445V335.87c-18.053.752-36.52 1.129-55 1.129-18.48 0-36.947-.377-55-1.129z"/></g>',
  bass: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M228.2 26.89c-15.2-.25-27.7 33.46-12.3 39.8l8.9 3.61 17.8-15.5-1.8-23.5-8.7-3.6c-1.1-.43-2.1-.68-3.2-.78h-.7zm134.4 7.92h-2.3c-7.4.3-15.3 2.12-23.3 5.75-21.2 9.67-43.6 32.67-59.7 75.74L174.4 394.7l-.1.2v.2c.9.5 3.6 1.9 8 4 9.8 4.8 24 15.7 26.1 38.8v.5l-1 55.6H304c-.1-17.6 1.4-34.5 8.1-51.5 11.7-29.4 39.3-54.9 97-77 0-.2 0 0 .1-.4.3-2 .4-6 0-11-1-10.1-4-24.9-8.6-42.2-9.2-34.7-24.8-80.2-42.4-124.9l-2.4-6.2 5.2-4.2c36.1-28.2 51.1-56.4 53.8-79.56 2.7-23.06-6.5-41.48-21.3-52.25-8.7-6.31-19.3-9.99-30.9-10.02zM260.5 52.44l.7 10.01-7.6 6.6 21.7 8.93c1.2-2.17 2.5-4.28 3.8-6.33l4-9.89zm69 18.56c8.8 0 16 7.16 16 16s-7.2 16-16 16-16-7.16-16-16 7.2-16 16-16zm-136.7 49.7c-15.2-.3-27.9 33.4-12.3 39.8l8.8 3.6 17.8-15.5-1.7-23.6-8.8-3.6c-1.1-.4-2.1-.6-3.1-.7zm32.2 25.5.7 10-7.6 6.6 19.9 8.1 6.2-16.8zm71.6 19.8c8.8 0 16 7.2 16 16s-7.2 16-16 16-16-7.2-16-16 7.2-16 16-16zm-139.8 50.6c-15.3-.3-27.8 33.4-12.4 39.8l8.9 3.6 17.8-15.5-1.8-23.6-8.7-3.6c-1.1-.4-2.1-.6-3.1-.7zm32.2 25.5.7 10-7.6 6.6 20.4 8.3 6.2-16.8zm67.2 19.3c8.8 0 16 7.2 16 16s-7.2 16-16 16-16-7.2-16-16 7.2-16 16-16zm-140.1 54.4c-15.2-.3-27.78 33.4-12.3 39.8l8.8 3.6 17.8-15.5-1.8-23.6-8.7-3.6c-1.1-.4-2.1-.6-3.1-.7zm32.2 25.5.7 10-7.6 6.6 22.8 9.3 6.8-16.6zm69.3 18.5c8.8 0 16 7.2 16 16s-7.2 16-16 16-16-7.2-16-16 7.2-16 16-16z"/></g>',
  guitar: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M152.6 26.32 137.2 441.9 256 486.4l118.8-44.5-15.4-415.58L256 41.09 152.6 26.32zM64 89c-36 0-36 78 0 78h9.51l13-39-13-39H64zm374.5 0-13 39 13 39h9.5c36 0 36-78 0-78h-9.5zM192 112a16 16 0 0 1 16 16 16 16 0 0 1-16 16 16 16 0 0 1-16-16 16 16 0 0 1 16-16zm128 0a16 16 0 0 1 16 16 16 16 0 0 1-16 16 16 16 0 0 1-16-16 16 16 0 0 1 16-16zm-217.6 7 2.1 6.2 1 2.8-3 9h28l.7-18h-28.8zm278.4 0 .7 18h28.1l-2.1-6.2-1-2.8 3-9h-28.7zM60 217c-36 0-36 78 0 78h9.51l13-39-13-39H60zm382.5 0-13 39 13 39h9.5c36 0 36-78 0-78h-9.5zM192 240a16 16 0 0 1 16 16 16 16 0 0 1-16 16 16 16 0 0 1-16-16 16 16 0 0 1 16-16zm128 0a16 16 0 0 1 16 16 16 16 0 0 1-16 16 16 16 0 0 1-16-16 16 16 0 0 1 16-16zm-221.56 7 2.06 6.2 1 2.8-3 9h27.3l.7-18H98.44zm287.06 0 .7 18h27.4l-2.1-6.2-1-2.8 3-9h-28zM56 345c-36 0-36 78 0 78h9.51l13-39-13-39H56zm390.5 0-13 39 13 39h9.5c36 0 36-78 0-78h-9.5zM192 368a16 16 0 0 1 16 16 16 16 0 0 1-16 16 16 16 0 0 1-16-16 16 16 0 0 1 16-16zm128 0a16 16 0 0 1 16 16 16 16 0 0 1-16 16 16 16 0 0 1-16-16 16 16 0 0 1 16-16zm-225.53 7 2.07 6.2.95 2.8-3 9h26.61l.6-18H94.47zm295.83 0 .6 18h26.7l-2.1-6.2-1-2.8 3-9h-27.2z"/></g>',
  piano: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M24 80v352h64V288H72V80H24zm96 0v208h-16v144h64V288h-16V80h-32zm80 0v208h-16v144h64V288h-16V80h-32zm80 0v208h-16v144h64V80h-48zm64 0v352h64V288h-16V80h-48zm96 0v208h-16v144h64V80h-48z"/></g>',
  other: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M385.853 21.083c-2.876-.097-4.956.287-6.693 1.058 4.438 38.628-3.264 69.792-21.635 99.469-19.234 31.07-49.316 60.967-88.852 100.502l-3.586 3.722 21.079 21.079 3.722-3.586c28.35-28.35 51.736-51.808 74.16-69.922a50.053 50.053 0 0 1 7.662-.918 47.688 47.688 0 0 1 2.582-.033c12.806.18 23.989 5.554 32.149 13.714 16.225 16.225 21.424 44.398-3.31 73.522-8.9-7.421-17.504-10.952-25.503-10.69a24.831 24.831 0 0 0-4.064.47c-10.692 2.138-17.328 9.532-22.984 15.189l-23.334 23.334 12.726 12.728 23.336-23.336c5.657-5.657 10.335-9.575 13.785-10.265a9.39 9.39 0 0 1 2.133-.188c2.622.075 6.14 1.307 11.451 5.711l-39.39 39.39 12.728 12.73 45.961-45.962c36.549-36.548 33.505-81.048 9.193-105.36-8.559-8.558-19.627-14.465-31.962-16.952 1.06-.683 2.12-1.356 3.183-2.014 29.677-18.371 60.841-26.073 99.469-21.635.771-1.737 1.155-3.817 1.058-6.693-.16-4.804-1.931-11.321-5.304-18.549-6.746-14.455-19.647-31.608-34.625-46.586-14.978-14.978-32.131-27.88-46.586-34.625-7.228-3.373-13.745-5.143-18.549-5.304zM217.492 202.584l-15.557 15.557 12.729 12.727 15.556-15.555-12.728-12.729zm23.486 24.596-14.143 14.143 111.723 111.722 14.143-14.14L240.978 227.18zm-50.309 2.227-15.556 15.556 12.728 12.727 15.557-15.555-12.729-12.728zm23.438 24.644-14.143 14.143L311.69 379.916l14.14-14.142L214.108 254.05zm-50.203 2.121-15.557 15.557 12.729 12.729L176.63 268.9l-12.726-12.729zm23.334 24.748-14.143 14.143 111.723 111.723 14.142-14.141L187.238 280.92zm-13.75 39.99L51.355 447.683l12.963 12.963 39.988-38.525c.004.092.001.186.006.279.877 17.378 8.833 33.331 20.701 45.2 11.868 11.867 27.822 19.822 45.2 20.698 17.377.877 35.967-5.7 51.673-21.406l48.791-48.79-12.728-12.73-42.414 42.415c-7.028-7.302-6.922-10.348-6-13.532.964-3.332 5.28-8.376 10.937-14.033l26.162-26.162-12.726-12.728-26.164 26.164c-5.657 5.656-12.655 11.926-15.5 21.755-2.715 9.379.623 20.584 10.172 30.83-10.453 7.99-21.092 10.756-31.297 10.24-12.41-.625-24.387-6.459-33.38-15.45-8.99-8.992-14.824-20.968-15.45-33.377-.334-6.622.726-13.428 3.681-20.246l65.117-62.735-17.6-17.601zM33.796 455.578l-12.728 12.728 22.627 22.627 12.728-12.728-22.627-22.627z"/></g>',
  turtle: '<path d="m12 10 2 4v3a1 1 0 0 0 1 1h2a1 1 0 0 0 1-1v-3a8 8 0 1 0-16 0v3a1 1 0 0 0 1 1h2a1 1 0 0 0 1-1v-3l2-4h4z"/><path d="M4.82 7.9 8 10"/><path d="M15.18 7.9 12 10"/><path d="M16.93 10H20a2 2 0 0 1 0 4H2"/>',
  rabbit: '<path d="M13 16a3 3 0 0 1 2.24 5"/><path d="M18 12h.01"/><path d="M18 21h-8a4 4 0 0 1-4-4 7 7 0 0 1 7-7h.2L9.6 6.4a1 1 0 1 1 2.8-2.8L15.8 7h.2c3.3 0 6 2.7 6 6v1a2 2 0 0 1-2 2h-1a3 3 0 0 0-3 3"/><path d="M20 8.54V4a2 2 0 1 0-4 0v3"/><path d="M7.612 12.524a3 3 0 1 0-1.6 4.3"/>',
  sparkles: '<path d="M9.9 15.5a2 2 0 0 0-1.4-1.4l-6.1-1.6a.5.5 0 0 1 0-1l6.1-1.6a2 2 0 0 0 1.4-1.4l1.6-6.1a.5.5 0 0 1 1 0l1.6 6.1a2 2 0 0 0 1.4 1.4l6.1 1.6a.5.5 0 0 1 0 1l-6.1 1.6a2 2 0 0 0-1.4 1.4l-1.6 6.1a.5.5 0 0 1-1 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/>',
  original: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  speaker: '<path d="M11 4.7a.7.7 0 0 0-1.2-.5L6.4 7.6A1.4 1.4 0 0 1 5.4 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.4a1.4 1.4 0 0 1 1 .4l3.4 3.4a.7.7 0 0 0 1.2-.5z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.4 18.4a9 9 0 0 0 0-12.8"/>',
};

function iconSvg(name) {
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${transportIcons[name]}</svg>`;
}

function setIcon(button, name) {
  button.innerHTML = iconSvg(name);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error?.message || "Не удалось выполнить запрос");
  return body;
}

function setFormMessage(text, isError = false) {
  formMessage.textContent = text;
  formMessage.classList.toggle("error", isError);
}

function openSearch() {
  searchOverlay.hidden = false;
  document.body.classList.add("search-open");
  window.setTimeout(() => inputField.focus(), 0);
}

function closeSearch({ restoreFocus = true } = {}) {
  if (searchOverlay.hidden) return;
  searchOverlay.hidden = true;
  document.body.classList.remove("search-open");
  if (restoreFocus) searchTrigger.focus();
}

const diskUsage = document.querySelector("#disk-usage");
const diskMessage = document.querySelector("#disk-message");
const cleanStorageButton = document.querySelector("#clean-storage");
const deleteAllButton = document.querySelector("#delete-all-projects");
let cleanableBytes = 0;
let projectsBytes = 0;

async function loadStorage() {
  try {
    const report = await requestJson("/api/v1/storage");
    cleanableBytes = report.unused_bytes + report.temp_bytes;
    const rows = [["Проекты", report.projects_bytes], ["Файлы без проекта", report.unused_bytes],
      ["Временные файлы", report.temp_bytes], ["Модели разделения", report.models_bytes]];
    diskUsage.replaceChildren(...rows.flatMap(([label, bytes]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const value = document.createElement("dd");
      value.textContent = formatBytes(bytes);
      return [term, value];
    }));
    cleanStorageButton.disabled = report.busy || cleanableBytes === 0;
    projectsBytes = report.projects_bytes;
    deleteAllButton.disabled = report.busy;
    deleteAllButton.textContent = projectsBytes ? `Удалить все проекты (${formatBytes(projectsBytes)})` : "Удалить все проекты";
    cleanStorageButton.textContent = cleanableBytes ? `Очистить ${formatBytes(cleanableBytes)}` : "Очистить";
    diskMessage.textContent = report.busy ? "Дождитесь окончания разделения: пока оно идёт, очистка и удаление недоступны." : "";
  } catch (error) {
    diskMessage.textContent = error.message;
  }
}

async function cleanStorage() {
  const confirmed = await askConfirm({ title: "Очистить хранилище?", text: `Файлы без проекта и временные файлы (${formatBytes(cleanableBytes)}) будут удалены. Проекты не пострадают.`, action: "Удалить" });
  if (!confirmed) return;
  cleanStorageButton.disabled = true;
  try {
    const result = await requestJson("/api/v1/storage/cleanup", { method: "POST", body: JSON.stringify({ confirmed: true }) });
    await loadStorage();
    diskMessage.textContent = `Освобождено ${formatBytes(result.freed_bytes)}.`;
  } catch (error) {
    diskMessage.textContent = error.message;
    cleanStorageButton.disabled = false;
  }
}

// Deletes every project one by one through the same route as the bin, so shared files and locked
// files are handled the same way.
async function deleteAllProjects() {
  let projects;
  try {
    projects = await requestJson("/api/v1/projects");
  } catch (error) {
    diskMessage.textContent = error.message;
    return;
  }
  if (!projects.length) {
    diskMessage.textContent = "Проектов нет.";
    return;
  }
  const freed = projectsBytes;
  if (!(await askConfirm({ title: `Удалить все проекты (${projects.length})?`, text: `Вместе с их аудио и дорожками — ${formatBytes(freed)}. Это нельзя отменить.`, action: "Удалить все" }))) return;
  deleteAllButton.disabled = cleanStorageButton.disabled = true;
  cleanupPlayback();  // Windows cannot delete a file the browser is still reading
  const failed = [];
  for (const project of projects) {
    try {
      await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}`, { method: "DELETE" });
    } catch (_error) {
      failed.push(project.title || project.input_value);
    }
  }
  selectedProjectId = null;
  selectedProjectTitle = "";
  await loadProjects();
  await loadStorage();
  diskMessage.textContent = failed.length ? `Не удалось удалить: ${failed.join(", ")}.` : `Удалено проектов: ${projects.length}. Освобождено ${formatBytes(freed)}.`;
}

// Settings open over the project (which keeps playing) and close on a click outside, Esc or ×.
function openSettings() {
  settingsOverlay.hidden = false;
  document.body.classList.add("search-open");
  history.replaceState(null, "", "#settings");
  loadStorage();
  loadModels();
}

function closeSettings() {
  if (settingsOverlay.hidden) return;
  settingsOverlay.hidden = true;
  document.body.classList.remove("search-open");
  history.replaceState(null, "", "#project");
}

// What the keydown handler below does, for the help window («?» in the header). Keep the two in step.
const HOTKEYS = [
  ["Воспроизвести / пауза", "Пробел, Enter"],
  ["Назад / вперёд на 5 секунд", "←, →"],
  ["В начало", "Home"],
  ["В начало и пауза", "Backspace"],
  ["Выбор дорожки: следующая / предыдущая", "↓, ↑"],
  ["Следующая / предыдущая дорожка по кругу", "Tab, Shift+Tab"],
  ["Открыть вкладку 1–9", "1–9"],
  ["Вкладка: предыдущая / следующая", "Q, W"],
  ["Выбрать Vocal, Drums, Bass, Guitar, Piano, Other", "V, D, B, G, P, O"],
  ["Solo выбранной дорожки", "S"],
  ["Mute выбранной дорожки", "M"],
  ["Громкость выбранной дорожки +10 % / −10 %", "+, −"],
  ["Открыть файл или найти песню", /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘F" : "Ctrl+F"],
  ["Ещё одна строка в поле поиска", "Shift+Enter"],
  ["Закрыть окно", "Esc"],
];

function openHelp() {
  const list = document.querySelector("#hotkey-list");
  if (!list.childElementCount) {
    list.innerHTML = HOTKEYS.map(([action, keys]) => `<div><dt>${action}</dt><dd>${keys.split(", ").map((key) => `<kbd>${key}</kbd>`).join(", ")}</dd></div>`).join("");
  }
  helpOverlay.hidden = false;
  document.body.classList.add("search-open");
}

function closeHelp() {
  if (helpOverlay.hidden) return;
  helpOverlay.hidden = true;
  document.body.classList.remove("search-open");
}

// The queue: what the worker does and what waits (GET /api/v1/queue), with a cancel button for each task.
const workingStates = new Set(["searching", "downloading", "separating", "running"]);
let queueTimer = null;

// The share done is the row's background fill, not a number.
function queueText(item) {
  const left = item.eta_seconds !== null && item.eta_seconds !== undefined ? ` · осталось ${formatMinutes(item.eta_seconds)}` : "";
  if (item.kind === "upgrade") return `Улучшение качества${item.status === "queued" ? " · в очереди" : ""}${left}`;
  return `${stateDescriptions[item.status] || item.status}${left}`;
}

function renderQueue(items) {
  queueTrigger.hidden = !items.length;
  if (!items.length) closeQueue();
  const current = items.find((item) => workingStates.has(item.status) && item.progress > 0);
  if (current) {
    queueTrigger.dataset.share = "true";
    queueTrigger.style.setProperty("--queue-progress", String(Math.max(2, Math.round(current.progress * 100))));
  } else {
    delete queueTrigger.dataset.share;
    queueTrigger.style.removeProperty("--queue-progress");
  }
  const label = items.length === 1 ? "Очередь: 1 задача" : `Очередь: ${items.length} ${items.length < 5 ? "задачи" : "задач"}`;
  queueTrigger.setAttribute("aria-label", label);
  queueTrigger.title = label;
  document.querySelector("#queue-list").replaceChildren(...items.map((item) => {
    const row = document.createElement("div");
    row.className = "queue-item";
    row.dataset.kind = item.kind;
    if (workingStates.has(item.status)) row.style.setProperty("--job-progress", `${Math.round((item.progress || 0) * 100)}%`);
    const title = document.createElement("span");
    title.className = "queue-title";
    title.textContent = item.title;
    title.title = item.title;
    const meta = document.createElement("span");
    meta.className = "queue-meta";
    meta.textContent = queueText(item);
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "icon-button";
    cancel.dataset.action = "cancel-task";
    cancel.setAttribute("aria-label", `Отменить: ${item.title}`);
    cancel.title = "Отменить";
    cancel.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
    cancel.addEventListener("click", () => cancelTask(item));
    row.append(title, meta, cancel);
    return row;
  }));
}

async function refreshQueue() {
  window.clearTimeout(queueTimer);
  let items = [];
  try {
    items = await requestJson("/api/v1/queue");
  } catch (_error) { /* the API is restarting: the next look shows it */ }
  renderQueue(items);
  queueTimer = window.setTimeout(refreshQueue, items.length ? 2000 : 10000);
}

// A task that runs loses its progress when cancelled, so that one asks first.
async function cancelTask(item) {
  const what = item.kind === "upgrade" ? `улучшение «${item.title}»` : `«${item.title}»`;
  if (workingStates.has(item.status) && !(await askConfirm({ title: `Отменить ${what}?`, text: "То, что уже сделано, будет потеряно.", action: "Отменить задачу" }))) return;
  try {
    if (item.kind === "upgrade") {
      await requestJson(`/api/v1/projects/${encodeURIComponent(item.project_id)}/upgrade`, { method: "DELETE" });
    } else {
      await requestJson(`/api/v1/jobs/${encodeURIComponent(item.job_id)}/cancel`, { method: "POST" });
    }
  } catch (error) {
    detailError.textContent = error.message;
    detailError.hidden = false;
  }
  await refreshQueue();
  await loadProjects();
}

function openQueue() {
  queuePopover.hidden = false;
  queueTrigger.setAttribute("aria-expanded", "true");
}

function closeQueue() {
  queuePopover.hidden = true;
  queueTrigger.setAttribute("aria-expanded", "false");
}

function activateScreen(name) {
  if (name === "settings") {
    openSettings();
  } else {
    closeSettings();
    loadProjects();
  }
}

function statusBucket(status) {
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  return status;
}

let projectPollTimer = null;
const finishedStates = new Set(["completed", "failed", "cancelled"]);

// While a job runs, the tab fills with its progress; poll until every project has finished.
function scheduleProjectPoll(projects) {
  if (projectPollTimer !== null || !projects.some((project) => !finishedStates.has(project.job_status))) return;
  projectPollTimer = window.setTimeout(() => {
    projectPollTimer = null;
    loadProjects();
  }, 1000);
}

function updateProjectTab(button, project) {
  const nameText = project.title || project.input_value;
  const state = stateDescriptions[project.job_status] || project.job_status;
  button.dataset.status = statusBucket(project.job_status);
  button.style.setProperty("--job-progress", `${Math.round((project.progress || 0) * 100)}%`);
  button.setAttribute("aria-selected", String(project.project_id === selectedProjectId));
  button.setAttribute("aria-label", `${nameText}. ${state}`);
  button.title = `${nameText} — ${state}`;
  button.firstElementChild.textContent = nameText;
}

// The open project's job, as last seen by the poll: drives its progress bar and the switch to the mixer.
let selectedJobState = null;

// Separation time for one minute of music: "~55 с" or "~11 мин".
function perMinuteOfSong(secondsPerSecond) {
  const seconds = secondsPerSecond * 60;
  return seconds < 60 ? `~${Math.max(5, Math.round(seconds / 5) * 5)} с` : `~${Math.round(seconds / 60)} мин`;
}

function formatMinutes(seconds) {
  return seconds < 60 ? "меньше минуты" : `~${Math.round(seconds / 60)} мин`;
}

function etaText(state) {
  if (state.eta_seconds === null || state.eta_seconds === undefined) return "";
  return state.job_status === "separating"
    ? ` · осталось ${formatMinutes(state.eta_seconds)}`
    : ` · разделение займёт ${formatMinutes(state.eta_seconds)}`;
}

function renderJobProgress(state) {
  let bar = playbackPanel.querySelector(".job-progress");
  if (!bar) {
    const label = document.createElement("p");
    label.className = "job-progress-label";
    bar = document.createElement("div");
    bar.className = "job-progress";
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.append(document.createElement("span"));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "icon-button job-cancel";
    cancel.dataset.action = "cancel-job";
    cancel.setAttribute("aria-label", "Отменить");
    cancel.title = "Отменить";
    setIcon(cancel, "close");
    cancel.addEventListener("click", () => cancelTask({
      kind: "job", job_id: selectedJobId, title: selectedProjectTitle, status: selectedJobState?.job_status || "searching" }));
    // The stage over the bar, ✕ right of it; under the tracks (the original audio plays meanwhile), alone before them.
    const status = document.createElement("div");
    status.className = "job-status";
    status.append(label, bar, cancel);
    playbackPanel.querySelector(".separate-again")?.remove();  // «Разделить» was pressed: progress again
    const mixer = playbackPanel.querySelector(".mixer-grid");
    if (activeControls && mixer) mixer.after(status);
    else playbackPanel.replaceChildren(status);
    playbackPanel.hidden = false;
  }
  const percent = Math.round((state.progress || 0) * 100);
  bar.firstElementChild.style.width = `${percent}%`;
  bar.setAttribute("aria-valuenow", String(percent));
  playbackPanel.querySelector(".job-progress-label").textContent =
    `${stateDescriptions[state.job_status] || state.job_status} · ${percent}%${etaText(state)}`;
}

function followSelectedJob(projects) {
  const selected = projects.find((project) => project.project_id === selectedProjectId);
  if (!selected) return;
  const wasRunning = selectedJobState && !finishedStates.has(selectedJobState.job_status);
  const sourceReady = (state) => state === "downloaded" || state === "separating";  // polling may miss «downloaded»
  const downloadedNow = sourceReady(selected.job_status) && !sourceReady(selectedJobState?.job_status);
  selectedJobState = selected;
  if (downloadedNow) {
    openProject(selected.project_id);  // the source audio is there: play it while the stems are made
  } else if (!finishedStates.has(selected.job_status)) {
    renderJobProgress(selected);
  } else if (wasRunning) {
    openProject(selected.project_id);  // finished while open: show the mixer (or the error)
  }
}

function renderProjects(projects) {
  scheduleProjectPoll(projects);
  followSelectedJob(projects);
  renderTabList(projects);
  const tabs = [...projectTabs.querySelectorAll(".project-tab")];
  // Same projects in the same order: update in place, so polling never resets hover, drag or scroll.
  if (tabs.length && tabs.length === projects.length && tabs.every((tab, index) => tab.dataset.projectId === projects[index].project_id)) {
    tabs.forEach((tab, index) => updateProjectTab(tab, projects[index]));
    return;
  }
  if (projects.length === 0) {
    const empty = document.createElement("p");
    empty.className = "tabs-empty";
    empty.textContent = "Проекты появятся здесь";
    projectTabs.replaceChildren(empty);
    selectedProjectId = null;
    selectedProjectTitle = "";
    projectEmpty.hidden = false;
    projectDetail.hidden = true;
    return;
  }
  projectTabs.replaceChildren(...projects.map((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-tab";
    button.dataset.projectId = project.project_id;
    button.dataset.jobId = project.job_id;
    button.setAttribute("role", "tab");
    const name = document.createElement("span");
    name.className = "project-tab-name";
    button.append(name);
    updateProjectTab(button, project);
    button.addEventListener("click", () => openProject(project.project_id));
    return button;
  }));
  updateTabFades();
}

// The list of every project (the button right of the tabs): a click opens one, ✕ deletes it after a question;
// one still being worked on is cancelled first.
function renderTabList(projects) {
  tabsTrigger.hidden = !projects.length;
  if (!projects.length) closeTabList();
  document.querySelector("#tabs-list").replaceChildren(...projects.map((project) => {
    const nameText = project.title || project.input_value;
    const row = document.createElement("div");
    row.className = "queue-item tabs-item";
    row.dataset.projectId = project.project_id;
    row.setAttribute("aria-current", String(project.project_id === selectedProjectId));
    if (!finishedStates.has(project.job_status)) row.style.setProperty("--job-progress", `${Math.round((project.progress || 0) * 100)}%`);
    const title = document.createElement("span");
    title.className = "queue-title";
    title.textContent = nameText;
    title.title = nameText;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button";
    remove.dataset.action = "delete-project";
    remove.setAttribute("aria-label", `Удалить: ${nameText}`);
    remove.title = "Удалить";
    remove.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      askToDelete(project, nameText);
    });
    row.addEventListener("click", () => {
      closeTabList();
      openProject(project.project_id);
    });
    row.append(title, remove);
    return row;
  }));
}

async function askToDelete(project, nameText) {
  if (!finishedStates.has(project.job_status)) {
    askConfirm({
      title: "Отменить и удалить проект?",
      text: `«${nameText}» ещё обрабатывается: обработка будет остановлена, проект и его файлы удалены.`,
      action: "Отменить и удалить",
      run: () => cancelAndDelete(project),
    });
    return;
  }
  try {
    // The list has no sizes; the project's own record has them, as for the bin.
    const detail = await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}`);
    askDeleteProject(project.project_id, nameText, { disk: detail.disk_bytes || 0, shared: detail.shared_bytes || 0 });
  } catch (error) {
    detailError.textContent = error.message;
    detailError.hidden = false;
  }
}

// The one deletion question, for the bin and for the project list.
function askDeleteProject(projectId, nameText, bytes) {
  const shared = bytes.shared
    ? ` ${formatBytes(bytes.shared)} общих файлов останутся: их использует другой проект.` : "";
  askConfirm({
    title: "Удалить проект?",
    text: `Проект «${nameText}», исходное аудио и все дорожки будут удалены. Освободится ${formatBytes(bytes.disk)}.${shared}`,
    action: "Удалить",
    run: () => deleteProject(projectId),
  });
}

// A running job cannot be deleted: cancel it, wait until the worker has stopped it (cancelled while downloading,
// the worker deletes the project itself), then delete what is left.
async function cancelAndDelete(project) {
  const answer = await requestJson(`/api/v1/jobs/${encodeURIComponent(project.job_id)}/cancel`, { method: "POST" });
  for (let attempt = 0; answer.state !== "deleted" && attempt < 120; attempt += 1) {
    const now = (await requestJson("/api/v1/projects")).find((item) => item.project_id === project.project_id);
    if (!now) break;  // the worker deleted it
    if (finishedStates.has(now.job_status)) return deleteProject(project.project_id);
    await new Promise((resolve) => { window.setTimeout(resolve, 500); });
  }
  if (answer.state !== "deleted" && (await requestJson("/api/v1/projects")).some((item) => item.project_id === project.project_id)) {
    throw new Error("Обработка ещё останавливается. Попробуйте удалить проект чуть позже.");
  }
  if (project.project_id === selectedProjectId) {
    cleanupPlayback();
    selectedProjectId = null;
    selectedProjectTitle = "";
  }
  await loadProjects();
}

function openTabList() {
  tabsPopover.hidden = false;
  tabsTrigger.setAttribute("aria-expanded", "true");
}

function closeTabList() {
  tabsPopover.hidden = true;
  tabsTrigger.setAttribute("aria-expanded", "false");
}

// Fades mark the edges where more tabs are hidden.
function updateTabFades() {
  const hidden = projectTabs.scrollWidth - projectTabs.clientWidth;
  projectTabs.classList.toggle("fade-left", projectTabs.scrollLeft > 1);
  projectTabs.classList.toggle("fade-right", projectTabs.scrollLeft < hidden - 1);
}

projectTabs.addEventListener("scroll", updateTabFades, { passive: true });
window.addEventListener("resize", updateTabFades);
// The mouse wheel scrolls the tab strip sideways.
projectTabs.addEventListener("wheel", (event) => {
  if (projectTabs.scrollWidth <= projectTabs.clientWidth) return;
  event.preventDefault();
  projectTabs.scrollLeft += Math.abs(event.deltaY) > Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
}, { passive: false });
// Dragging scrolls the strip; past a 5px threshold the drag no longer counts as a tab click.
let tabDrag = null;
let suppressTabClick = false;
projectTabs.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || event.pointerType === "touch") return; // touch already scrolls natively
  tabDrag = { id: event.pointerId, x: event.clientX, left: projectTabs.scrollLeft, moved: false };
});
projectTabs.addEventListener("pointermove", (event) => {
  if (!tabDrag || event.pointerId !== tabDrag.id) return;
  const dx = event.clientX - tabDrag.x;
  if (!tabDrag.moved && Math.abs(dx) > 5) {
    tabDrag.moved = true;
    projectTabs.setPointerCapture(event.pointerId);
    projectTabs.classList.add("dragging");
  }
  if (tabDrag.moved) projectTabs.scrollLeft = tabDrag.left - dx;
});
function endTabDrag() {
  if (!tabDrag) return;
  if (tabDrag.moved) {
    suppressTabClick = true;
    window.setTimeout(() => { suppressTabClick = false; }, 0);
  }
  projectTabs.classList.remove("dragging");
  tabDrag = null;
}
projectTabs.addEventListener("pointerup", endTabDrag);
projectTabs.addEventListener("pointercancel", endTabDrag);
projectTabs.addEventListener("click", (event) => {
  if (!suppressTabClick) return;
  event.preventDefault();
  event.stopPropagation();
}, true);

const SVG_NS = "http://www.w3.org/2000/svg";

// One filled shape: the peak envelope on top and its mirror below, in a 1024x100 viewBox that the
// browser scales to any width (preserveAspectRatio="none"), so nothing overlaps at any size.
function waveformPath(levels) {
  const step = 1024 / Math.max(1, levels.length);
  const point = (peak, index, sign) => `${((index + 0.5) * step).toFixed(2)} ${(50 + sign * Math.min(1, peak) * 48).toFixed(2)}`;
  const top = levels.map((peak, index) => point(peak, index, -1));
  const bottom = levels.map((peak, index) => point(peak, index, 1)).reverse();
  return `M0 50L${top.join("L")}L1024 50L${bottom.join("L")}Z`;
}

async function drawWaveform(svg, assetId) {
  try {
    const waveform = await requestJson(`/api/v1/assets/${encodeURIComponent(assetId)}/waveform`);
    if (waveform.asset_id !== assetId) return;
    const axis = document.createElementNS(SVG_NS, "line");
    axis.setAttribute("class", "waveform-axis");
    axis.setAttribute("x1", "0");
    axis.setAttribute("y1", "50");
    axis.setAttribute("x2", "1024");
    axis.setAttribute("y2", "50");
    const shape = document.createElementNS(SVG_NS, "path");
    shape.setAttribute("class", "waveform-shape");
    shape.setAttribute("d", waveformPath(waveform.levels));
    svg.replaceChildren(axis, shape);
  } catch (_error) {
    svg.setAttribute("aria-label", "Форма волны недоступна");
  }
}

function cleanupPlayback() {
  playbackSetupId += 1;
  if (activeTransport) activeTransport.stop();
  activeTransport = null;
  activeMixer = null;
  activeControls = null;
  if (activeAudioContext && activeAudioContext.state !== "closed") activeAudioContext.close().catch(() => {});
  activeAudioContext = null;
  // Detached <audio> keeps downloading and holds the browser's per-host connections; abort it.
  playbackPanel.querySelectorAll("audio").forEach((audio) => {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  });
  playbackPanel.replaceChildren();
  playbackPanel.hidden = true;
  detailControls.replaceChildren();
  detailTransport.replaceChildren();
  detailTime.textContent = "";
  playbackKey = "";
}

const STEM_LABELS = { vocals: "Vocal", drums: "Drums", bass: "Bass", guitar: "Guitar", piano: "Piano", other: "Other", original: "Original" };

// Files of a finished project were removed from disk by hand: say which, and offer to make them again.
function renderMissingFiles(project, missing) {
  const message = document.createElement("p");
  message.className = "playback-message";
  const names = missing.map((asset) => (asset.asset_type === "stem" ? STEM_LABELS[asset.stem_name] || asset.stem_name : "исходное аудио"));
  message.textContent = `С диска удалены файлы проекта: ${names.join(", ")}.`;
  if (project.input_type === "local_file") {  // nothing to download again: the user has the file
    message.textContent += " Удалите проект и откройте файл заново.";
    playbackPanel.replaceChildren(message);
    playbackPanel.hidden = false;
    return;
  }
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "primary-button";
  retry.textContent = "Разделить заново";
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    try {
      // The broken project goes (what is left of it too) and the same song is queued again.
      await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}`, { method: "DELETE" });
      const model = project.job.separation_options && project.job.separation_options.model_name;
      const created = await requestJson("/api/v1/batches", {
        method: "POST",
        body: JSON.stringify(model ? { inputs: [project.input_value], model_name: model } : { inputs: [project.input_value] }),
      });
      selectedProjectId = null;
      await loadProjects(created.job_ids[0]);
    } catch (error) {
      message.textContent = error.message;
      retry.disabled = false;
    }
  });
  playbackPanel.replaceChildren(message, retry);
  playbackPanel.hidden = false;
}

const sourceAudio = (project) => project.assets.find((asset) => asset.asset_type === "source_audio" && asset.available);

function renderSeparateAgain(project) {
  const box = document.createElement("div");
  box.className = "separate-again";
  const note = document.createElement("p");
  note.className = "playback-message";
  note.textContent = project.job.status === "cancelled" ? "Разделение отменено." : "Разделить не удалось.";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "primary-button";
  button.dataset.action = "separate";
  button.textContent = "Разделить";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}/separate`, { method: "POST" });
      await loadProjects();
      refreshQueue();
    } catch (error) {
      note.textContent = error.message;
      button.disabled = false;
    }
  });
  box.append(note, button);
  playbackPanel.prepend(box);
}

function renderProject(project) {
  // Re-rendering the same project (tab click, job-finished refresh) must not restart playback;
  // rebuild only when the project or its stem files change.
  const key = `${project.project_id}:${project.assets.map((a) => `${a.asset_id}${a.available ? "" : "?"}`).join(",")}`;
  const rebuild = key !== playbackKey;
  if (rebuild) cleanupPlayback();
  selectedProjectId = project.project_id;
  selectedProjectTitle = project.title || project.input_value;
  selectedJobId = project.job.job_id;
  projectEmpty.hidden = true;
  projectDetail.hidden = false;
  detailTitle.textContent = selectedProjectTitle;
  detailTitle.title = selectedProjectTitle;
  markClippedTitle();
  detailError.textContent = project.job.error_summary || "";
  detailError.hidden = !project.job.error_summary;
  deleteProjectButton.dataset.projectId = project.project_id;
  selectedProjectBytes = { disk: project.disk_bytes || 0, shared: project.shared_bytes || 0 };
  detailSize.textContent = formatBytes(selectedProjectBytes.disk + selectedProjectBytes.shared);
  detailSize.title = "Место на диске: исходное аудио и дорожки";
  if (rebuild) {
    playbackKey = key;
    const missing = project.assets.filter((asset) => !asset.available);
    if (!finishedStates.has(project.job.status)) {
      const known = selectedJobState && selectedJobState.project_id === project.project_id ? selectedJobState : null;
      renderJobProgress(known || { job_status: project.job.status, progress: 0 });
      // Downloaded: one track, «Original», to check it is the right song while it is separated.
      const source = sourceAudio(project);
      if (source) setupProjectPlayback([{ ...source, asset_type: "stem", stem_name: "original" }], ++playbackSetupId, 0, project, true);
    } else if (["cancelled", "failed"].includes(project.job.status) && sourceAudio(project)) {
      // Stopped before its stems: listen to the song and separate it again («Разделить»).
      setupProjectPlayback([{ ...sourceAudio(project), asset_type: "stem", stem_name: "original" }], ++playbackSetupId, 0, project, true);
    } else if (project.job.status === "completed" && missing.some((asset) => asset.asset_type === "stem")) {
      renderMissingFiles(project, missing);
    } else {
      setupProjectPlayback(project.assets, ++playbackSetupId, project.start_seconds || 0, project);
    }
  }
  document.querySelectorAll(".project-tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.projectId === project.project_id));
  });
}

async function setupProjectPlayback(assets, setupId, startOffset = 0, project = {}, original = false) {
  const stemOrder = ["vocals", "drums", "bass", "guitar", "piano", "other", "original"];
  const stemLabels = STEM_LABELS;
  const stemsByName = new Map(assets.filter((asset) => asset.asset_type === "stem").map((asset) => [asset.stem_name, asset]));
  // Four-stem models have no guitar and piano rows.
  const stems = stemOrder.map((name) => stemsByName.get(name)).filter(Boolean);
  const heading = document.createElement("p");
  heading.className = "playback-message";
  if (!stems.length || stems.some((asset) => !asset.available || (!original && !["wav", "flac"].includes(asset.media_format)))) {
    heading.textContent = "Для синхронного воспроизведения нужны все дорожки проекта.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
    return;
  }

  try {
    const { MixerSession, SynchronizedTransport, Metronome, PlayheadClock, StretchEngine, countInPlan, formatBpm, metronomeGain, rhythmFound, snapFaderPercent, tempoAt } = await import("./playback.mjs");
    const projectId = selectedProjectId;
    if (setupId !== playbackSetupId) return;
    const tracks = stems.map((asset) => {
      const audio = document.createElement("audio");
      audio.preload = "metadata";
      // Browsers allow six HTTP/1.1 connections per host, and six buffering stems would take
      // them all; loading stems from the other loopback name keeps API calls responsive.
      const path = `/api/v1/assets/${encodeURIComponent(asset.asset_id)}`;
      const url = new URL(path, window.location.href);
      url.hostname = { "127.0.0.1": "localhost", localhost: "127.0.0.1" }[url.hostname] || url.hostname;
      audio.crossOrigin = "anonymous";
      audio.src = url.href;
      audio.addEventListener("error", () => {
        if (audio.getAttribute("src") === url.href && url.href !== new URL(path, window.location.href).href) audio.src = path;
      }, { once: true });
      audio.setAttribute("aria-label", stemLabels[asset.stem_name]);
      audio.dataset.stem = asset.stem_name;
      return audio;
    });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("Web Audio is not supported in this browser");
    // The stems' own rate (both models write 44.1 kHz). At the device's rate (usually 48 kHz) Chrome resamples
    // every <audio> stem on its own and the six no longer add up cleanly: crackle on bright sounds. At 44.1 kHz
    // the mix reaches the output unchanged and is resampled once, as a whole, by the audio output.
    const audioContext = new AudioContextClass({ sampleRate: 44100 });
    // «Общая»: every stem (streamed or stretched, both end in its gain node) passes it; the metronome does not.
    const masterGain = audioContext.createGain();
    masterGain.connect(audioContext.destination);
    const gainNodes = tracks.map((audio) => {
      const source = audioContext.createMediaElementSource(audio);
      const gain = audioContext.createGain();
      source.connect(gain);
      gain.connect(masterGain);
      audio.volume = 1;
      audio.dataset.effectiveGain = "1";
      return gain;
    });
    activeAudioContext = audioContext;
    const mixerRowElements = [];
    const mixer = new MixerSession(gainNodes, (values, audible) => {
      tracks.forEach((audio, index) => { audio.dataset.effectiveGain = String(values[index]); });
      mixerRowElements.forEach((row, index) => { row.dataset.silenced = String(!audible[index]); });
    });
    activeMixer = mixer;

    const time = detailTime;
    // One playhead layer over the waveform column of all stem rows; it is the position slider
    // for both pointer and keyboard.
    const playheadLayer = document.createElement("div");
    playheadLayer.className = "playhead-layer";
    playheadLayer.dataset.control = "seek";
    playheadLayer.tabIndex = 0;
    playheadLayer.setAttribute("role", "slider");
    playheadLayer.setAttribute("aria-label", "Позиция воспроизведения");
    playheadLayer.setAttribute("aria-valuemin", "0");
    playheadLayer.setAttribute("aria-valuemax", "0");
    playheadLayer.setAttribute("aria-valuenow", "0");
    const playhead = document.createElement("span");
    playhead.className = "playhead";
    playheadLayer.append(playhead);
    // One screen pixel wide, on whole screen pixels: at 125-150 % scaling a 1px line at a fractional position was
    // drawn one or two pixels wide by turns (it flickered). A transform moves it without a layout.
    const playheadClock = new PlayheadClock();
    let playheadRatio = 0;
    let layerBox = { left: 0, width: 0 };
    const drawPlayhead = () => {
      const scale = window.devicePixelRatio || 1;
      const x = Math.round((layerBox.left + playheadRatio * layerBox.width) * scale) / scale - layerBox.left;
      playhead.style.width = `${1 / scale}px`;
      playhead.style.transform = `translateX(${x}px)`;
    };
    new ResizeObserver(() => {
      const box = playheadLayer.getBoundingClientRect();
      layerBox = { left: box.left + window.scrollX, width: box.width };
      drawPlayhead();
    }).observe(playheadLayer);
    let duration = 0;
    const formatTime = (seconds) => {
      const safe = Math.max(0, Math.floor(seconds || 0));
      return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
    };
    const updateTime = (seconds, playing = false, rate = 1) => {
      const shown = playheadClock.position(seconds, performance.now(), playing, rate);
      playheadRatio = duration > 0 ? Math.min(1, Math.max(0, shown / duration)) : 0;
      drawPlayhead();
      playheadLayer.setAttribute("aria-valuenow", seconds.toFixed(1));
      playheadLayer.setAttribute("aria-valuetext", formatTime(seconds));
      time.textContent = `${formatTime(seconds)} / ${formatTime(duration)}`;
    };
    const play = document.createElement("button");
    play.type = "button";
    play.className = "transport-button";
    play.dataset.action = "toggle";
    setIcon(play, "play");
    play.setAttribute("aria-label", "Воспроизвести");
    play.title = "Воспроизвести";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "transport-button";
    reset.dataset.action = "reset";
    setIcon(reset, "start");
    reset.setAttribute("aria-label", "В начало и пауза");
    reset.title = "В начало и пауза";
    const countIn = document.createElement("button");
    countIn.type = "button";
    countIn.className = "transport-button";
    countIn.dataset.action = "count-in";
    setIcon(countIn, "countin");
    countIn.setAttribute("aria-label", "Отсчёт на четыре и воспроизведение");
    countIn.title = "Отсчёт на четыре и воспроизведение";
    const buttons = document.createElement("div");
    buttons.className = "playback-buttons";
    buttons.append(play, countIn, reset);
    countIn.hidden = original;  // no beats before the stems exist
    const error = document.createElement("span");
    error.className = "playback-error";
    const mixerRows = document.createElement("div");
    mixerRows.className = "mixer-grid";

    const updateTransportButton = (isPlaying) => {
      setIcon(play, isPlaying ? "pause" : "play");
      play.setAttribute("aria-label", isPlaying ? "Пауза" : "Воспроизвести");
      play.title = isPlaying ? "Пауза" : "Воспроизвести";
    };
    let selectedStem = 0;
    const selectStem = (index) => {
      selectedStem = Math.min(stems.length - 1, Math.max(0, index));
      mixerRowElements.forEach((row, i) => { row.dataset.selected = String(i === selectedStem); });
    };
    // Metronome: its own bus with a limiter, so a loud click never distorts; the project gain is untouched.
    const metronomeBus = audioContext.createGain();
    const limiter = audioContext.createDynamicsCompressor();
    limiter.threshold.value = -2;
    limiter.ratio.value = 20;
    limiter.attack.value = 0.001;
    limiter.release.value = 0.05;
    metronomeBus.connect(limiter);
    limiter.connect(audioContext.destination);
    const metronome = new Metronome(audioContext, metronomeBus);
    let metronomeOn = false;
    let songTempo = null;  // [[time, bpm], ...] from the analysis, shown next to the metronome
    let noRhythm = false;  // said where the tempo would be: the icon-only button has no room for it
    const bpmLabel = document.createElement("span");
    bpmLabel.className = "metronome-bpm";
    const showTempo = (seconds) => {
      const text = noRhythm ? "Ритм не найден" : formatBpm(tempoAt(songTempo, seconds), metronome.rate);
      if (bpmLabel.textContent !== text) bpmLabel.textContent = text;  // changes at most every four beats
    };
    const transport = new SynchronizedTransport(tracks, {
      onTimeUpdate: (seconds) => {
        updateTime(seconds, transport.playing, transport.rate);
        showTempo(seconds);
        if (metronomeOn && transport.playing) metronome.update(seconds);
        else metronome.reset();
        metronomeButton.dataset.clicks = String(metronome.clicks);
      },
      onEnded: () => {
        metronome.reset();
        updateTransportButton(false);
      },
      beforePlay: () => audioContext.resume(),
      startOffset,
    });
    activeTransport = transport;
    const seekFromPointer = (event, element) => {
      const rect = element.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      if (duration > 0) transport.seek(ratio * duration);
    };
    const attachPointerSeek = (element) => {
      element.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        element.setPointerCapture(event.pointerId);
        seekFromPointer(event, element);
      });
      element.addEventListener("pointermove", (event) => {
        if (element.hasPointerCapture(event.pointerId)) seekFromPointer(event, element);
      });
    };
    // The layer covers the whole waveform column, gaps included, so a click anywhere there seeks.
    attachPointerSeek(playheadLayer);

    stems.forEach((asset, index) => {
      const row = document.createElement("div");
      row.className = "mixer-row";
      mixerRowElements.push(row);
      row.addEventListener("pointerdown", () => selectStem(index));
      // Explicit rows: the playhead layer spans all stem rows, which would push auto-placed rows down.
      row.style.gridRow = String(index + 1);
      row.dataset.stem = asset.stem_name;
      const left = document.createElement("div");
      left.className = "mixer-left";
      const name = document.createElement("span");
      name.className = "mixer-name";
      // On a narrow screen only the first letter shows (CSS), after the icon; the letter is also the stem's hotkey.
      const label = stemLabels[asset.stem_name] || asset.stem_name;
      name.innerHTML = `${transportIcons[asset.stem_name] ? iconSvg(asset.stem_name) : ""}<span class="mixer-label" data-short="${label[0]}">${label}</span>`;
      const switches = document.createElement("div");
      switches.className = "mixer-switches";
      const solo = document.createElement("button");
      solo.type = "button";
      solo.className = "mixer-toggle";
      solo.textContent = "S";
      solo.dataset.control = "solo";
      solo.setAttribute("aria-label", `Solo: ${stemLabels[asset.stem_name]}`);
      solo.setAttribute("aria-pressed", "false");
      solo.addEventListener("click", () => {
        const selected = solo.getAttribute("aria-pressed") !== "true";
        solo.setAttribute("aria-pressed", String(selected));
        mixer.setSolo(index, selected);
      });
      const mute = document.createElement("button");
      mute.type = "button";
      mute.className = "mixer-toggle";
      mute.textContent = "M";
      mute.dataset.control = "mute";
      mute.setAttribute("aria-label", `Mute: ${stemLabels[asset.stem_name]}`);
      mute.setAttribute("aria-pressed", "false");
      mute.addEventListener("click", () => {
        const muted = mute.getAttribute("aria-pressed") !== "true";
        mute.setAttribute("aria-pressed", String(muted));
        mixer.setMute(index, muted);
      });
      switches.append(solo, mute);
      const fader = document.createElement("div");
      fader.className = "mixer-fader";
      const gain = document.createElement("input");
      gain.type = "range";
      gain.min = "0";
      gain.max = "150";
      gain.step = "1";
      gain.value = "100";
      // Filled part of the custom track, as a share of the 0-150 % range.
      const showFill = () => gain.style.setProperty("--fill", `${(Number(gain.value) / 150) * 100}%`);
      showFill();
      gain.dataset.control = "gain";
      gain.tabIndex = -1; // volume is mouse-only; arrow keys belong to the global hotkeys
      gain.setAttribute("aria-label", `Громкость ${stemLabels[asset.stem_name]}`);
      gain.addEventListener("input", () => {
        const percent = snapFaderPercent(Number(gain.value));
        gain.value = String(percent);
        showFill();
        mixer.setGain(index, percent / 100);
      });
      fader.append(gain);
      const heading = document.createElement("div");
      heading.className = "mixer-heading";
      heading.append(name, switches);
      left.append(heading, fader);
      const wave = document.createElement("div");
      wave.className = "mixer-waveform";
      const waveSvg = document.createElementNS(SVG_NS, "svg");
      waveSvg.setAttribute("class", "waveform-preview");
      waveSvg.setAttribute("viewBox", "0 0 1024 100");
      waveSvg.setAttribute("preserveAspectRatio", "none");
      waveSvg.setAttribute("role", "img");
      waveSvg.setAttribute("aria-label", `Форма волны: ${stemLabels[asset.stem_name]}`);
      wave.append(waveSvg);
      if (!original) drawWaveform(waveSvg, asset.asset_id);  // none for the source audio (WAV/FLAC only)
      row.append(left, wave);
      mixerRows.append(row);
    });

    // The master volume in the header over the mixer column: 0-100 % for the whole mix, remembered for this project.
    const masterRow = document.createElement("div");
    masterRow.className = "mixer-master";  // not a .mixer-row: the stem rows are counted and selected by that class
    const masterName = document.createElement("span");
    masterName.className = "mixer-name";
    masterName.innerHTML = iconSvg("speaker");  // the fader carries the name «Общая громкость»
    const masterFader = document.createElement("div");
    masterFader.className = "mixer-fader master-fader";
    const master = document.createElement("input");
    master.type = "range";
    master.min = "0";
    master.max = "100";
    master.step = "1";
    master.dataset.control = "master-volume";
    master.tabIndex = -1;
    master.setAttribute("aria-label", "Общая громкость");
    const masterKey = `audio-stems-master-volume:${projectId}`;
    let savedMaster = "100";
    try { savedMaster = localStorage.getItem(masterKey) || "100"; } catch (_error) { /* storage blocked */ }
    master.value = savedMaster;
    const applyMaster = () => {
      masterGain.gain.value = Number(master.value) / 100;
      master.style.setProperty("--fill", `${master.value}%`);
      playbackPanel.dataset.masterGain = String(Number(master.value) / 100);  // the AudioParam is float32
    };
    applyMaster();
    master.addEventListener("input", () => {
      applyMaster();
      storeSetting(masterKey, master.value);
    });
    masterFader.append(master);
    masterRow.append(masterName, masterFader);

    let countInTimer = null;
    const cancelCountIn = () => {
      window.clearTimeout(countInTimer);
      countInTimer = null;
      countIn.removeAttribute("aria-pressed");
      metronome.reset();  // drops the count-in clicks that have not sounded yet
    };
    const togglePlayback = async () => {
      error.textContent = "";
      if (countInTimer !== null) {  // Space or ▶ during the count-in stops it
        cancelCountIn();
        return;
      }
      if (transport.playing) {
        transport.pause();
        metronome.reset();
        updateTransportButton(false);
        return;
      }
      if (decodedReady) {
        await decodedReady;
        if (setupId !== playbackSetupId) return;
      }
      try {
        await transport.play();
        updateTransportButton(transport.playing);
      } catch (_reason) {
        updateTransportButton(false);
        error.textContent = "Браузер не разрешил запустить все дорожки. Нажмите ▶ ещё раз.";
      }
    };
    play.addEventListener("click", togglePlayback);
    reset.addEventListener("click", () => {
      if (countInTimer !== null) cancelCountIn();
      transport.stop();
      metronome.reset();
      updateTransportButton(false);
    });

    // Footer under the tracks: the metronome and its volume.
    const footer = document.createElement("div");
    footer.className = "project-footer";
    const metronomeButton = document.createElement("button");
    metronomeButton.type = "button";
    metronomeButton.className = "footer-toggle metronome-toggle";
    metronomeButton.setAttribute("aria-pressed", "false");
    setIcon(metronomeButton, "metronome");
    // Icon only: the name (and the analysis progress, also shown as the fill) is the tooltip.
    const nameMetronome = (text) => {
      metronomeButton.title = text;
      metronomeButton.setAttribute("aria-label", text);
    };
    nameMetronome("Метроном");
    const volumeWrap = document.createElement("div");
    volumeWrap.className = "mixer-fader footer-fader";
    const volume = document.createElement("input");
    volume.type = "range";
    volume.min = "0";
    volume.max = "100";
    volume.step = "1";
    volume.dataset.control = "metronome-volume";
    volume.tabIndex = -1;
    volume.setAttribute("aria-label", "Громкость метронома");
    let savedVolume = "50";
    try { savedVolume = localStorage.getItem("audio-stems-metronome-volume") || "50"; } catch (_error) { /* storage blocked */ }
    volume.value = savedVolume;
    const showVolume = () => {
      volume.style.setProperty("--fill", `${volume.value}%`);
      metronomeBus.gain.value = metronomeGain(volume.value);
    };
    showVolume();
    volume.addEventListener("input", () => {
      showVolume();
      storeSetting("audio-stems-metronome-volume", volume.value);
    });
    volumeWrap.append(volume);

    // Speed 25-150 % in 5 % steps, pitch kept. The first change decodes the stems (about half a GB for
    // a 4-minute song, released with the project) and plays them through Signalsmith Stretch.
    const speedWrap = document.createElement("div");
    speedWrap.className = "footer-speed";
    const slower = document.createElement("span");
    slower.className = "speed-end";
    slower.innerHTML = iconSvg("turtle");
    const faster = document.createElement("span");
    faster.className = "speed-end";
    faster.innerHTML = iconSvg("rabbit");
    const speedFader = document.createElement("div");
    speedFader.className = "mixer-fader speed-fader";
    const speed = document.createElement("input");
    speed.type = "range";
    speed.min = "25";
    speed.max = "150";
    speed.step = "5";
    speed.value = "100";
    speed.dataset.control = "speed";
    speed.tabIndex = -1;
    speed.setAttribute("aria-label", "Скорость воспроизведения");
    const speedValue = document.createElement("span");  // a tooltip over the thumb (hover, drag, preparing)
    speedValue.className = "footer-speed-value";
    speedValue.setAttribute("aria-hidden", "true");  // the slider itself reports its value
    const scale = document.createElement("div");
    scale.className = "speed-scale";
    scale.setAttribute("aria-hidden", "true");
    scale.innerHTML = [25, 100, 150].map((value) => `<span style="left: ${((value - 25) / 125) * 100}%">${value}%</span>`).join("");
    speedFader.append(scale, speed, speedValue);
    speedWrap.append(slower, speedFader, faster);
    let stretchLoading = null;
    const loadStretch = () => {
      stretchLoading = stretchLoading || (async () => {
        // Three steps per stem (downloaded, decoded, handed to its node), shown as a fill on the block.
        let steps = 0;
        const step = () => {
          steps += 1;
          const percent = Math.round((steps / (3 * tracks.length)) * 100);
          speedWrap.style.setProperty("--job-progress", `${percent}%`);
          speedValue.textContent = `подготовка ${percent}%`;
        };
        speedWrap.dataset.loading = "true";
        speedWrap.style.setProperty("--job-progress", "0%");
        speedValue.textContent = "подготовка 0%";
        const { default: createStretch } = await import("./vendor/signalsmith-stretch-1.3.2.mjs");
        const decoded = await Promise.all(tracks.map(async (audio) => {
          const response = await fetch(audio.currentSrc || audio.src);
          const data = await response.arrayBuffer();
          step();
          const buffer = await audioContext.decodeAudioData(data);
          step();
          return buffer;
        }));
        transport.stretch = await StretchEngine.create(audioContext, createStretch, decoded, gainNodes, step);
        delete speedWrap.dataset.loading;
        speedWrap.style.removeProperty("--job-progress");
      })().catch((reason) => {
        stretchLoading = null;
        delete speedWrap.dataset.loading;
        speedWrap.style.removeProperty("--job-progress");
        error.textContent = `Не удалось включить изменение скорости: ${reason.message}`;
        throw reason;
      });
      return stretchLoading;
    };
    const showSpeed = () => {
      speed.style.setProperty("--fill", `${((Number(speed.value) - 25) / 125) * 100}%`);
      speedFader.style.setProperty("--share", String((Number(speed.value) - 25) / 125));
      if (!speedWrap.dataset.loading) speedValue.textContent = `${speed.value}%`;
    };
    const applySpeed = async () => {
      showSpeed();
      const rate = Number(speed.value) / 100;
      if (rate !== 1 && !transport.stretch) {
        try { await loadStretch(); } catch (_reason) { speed.value = "100"; showSpeed(); return; }
        if (setupId !== playbackSetupId) return;
      }
      const target = Number(speed.value) / 100;  // the fader may have moved while loading
      metronome.reset();  // clicks already scheduled were timed for the old speed
      metronome.rate = target;
      showTempo(transport.currentTime);  // the tempo heard at the new speed
      await transport.setPlaybackRate(target);
      speedWrap.dataset.engine = transport.stretching ? "stretch" : "stream";
      showSpeed();
    };
    speed.addEventListener("input", applySpeed);
    speed.addEventListener("dblclick", () => {
      speed.value = "100";
      applySpeed();
    });
    showSpeed();

    // ?engine=decoded, a trial for Safari on iPhone (it resumes streamed stems one after another, so they drift
    // apart after a pause): the decoded stems through the stretch engine at every speed, all on the AudioContext
    // clock. Prepared at once; ▶ and ▶4 wait for it; if it fails the stems stream as usual.
    const decodedNote = document.createElement("p");
    decodedNote.className = "playback-message decoded-note";
    let decodedReady = null;
    if (new URLSearchParams(location.search).get("engine") === "decoded") {
      const started = performance.now();
      decodedNote.textContent = "Декодированный режим: подготовка…";
      decodedReady = loadStretch().then(() => {
        if (setupId !== playbackSetupId) return;
        transport.alwaysStretch = true;
        if (!transport.playing) transport.seek(transport.currentTime);  // the engine takes the paused position
        speedWrap.dataset.engine = "stretch";
        decodedNote.textContent = `Декодированный режим: готово за ${((performance.now() - started) / 1000).toFixed(1).replace(".", ",")} с`;
      }).catch(() => {
        decodedReady = null;
        decodedNote.textContent = "Декодированный режим не включился: дорожки играют как обычно.";
      });
    }

    // «Улучшить качество»: the worker separates the song again with the best model while this project
    // keeps playing; when the new stems are ready the player switches to them at the same position.
    const upgradeButton = document.createElement("button");
    upgradeButton.type = "button";
    upgradeButton.className = "footer-toggle footer-upgrade";
    upgradeButton.dataset.action = "upgrade";
    upgradeButton.innerHTML = `${iconSvg("sparkles")}<span></span>`;
    const upgradeText = upgradeButton.querySelector("span");
    const upgradeCancel = document.createElement("button");  // only while the upgrade waits or runs
    upgradeCancel.type = "button";
    upgradeCancel.className = "icon-button upgrade-cancel";
    upgradeCancel.dataset.action = "cancel-upgrade";
    upgradeCancel.setAttribute("aria-label", "Отменить улучшение");
    upgradeCancel.title = "Отменить улучшение";
    upgradeCancel.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
    upgradeCancel.hidden = true;
    let upgradeStatus = "none";
    upgradeCancel.addEventListener("click", () => cancelTask({ kind: "upgrade", project_id: projectId, title: selectedProjectTitle, status: upgradeStatus }));
    const showUpgrade = (state) => {
      const busy = state.status === "queued" || state.status === "running";
      upgradeStatus = state.status;
      upgradeCancel.hidden = !busy;
      upgradeButton.disabled = Boolean(state.best) || busy;
      if (busy) upgradeButton.dataset.analyzing = "true";
      else delete upgradeButton.dataset.analyzing;
      // Only while it runs: an inline value outlives the stylesheet's «not analysing → 0 %», so a project upgraded
      // here kept a full fill on its finished button while one separated with the best model had none.
      if (busy) upgradeButton.style.setProperty("--job-progress", `${Math.round((state.progress || 0) * 100)}%`);
      else upgradeButton.style.removeProperty("--job-progress");
      const left = state.eta_seconds !== null && state.eta_seconds !== undefined ? ` · осталось ${formatMinutes(state.eta_seconds)}` : "";
      const icon = state.best ? "check" : "sparkles";  // done: a check mark instead of the sparkles
      if (upgradeButton.dataset.icon !== icon) {
        upgradeButton.dataset.icon = icon;
        upgradeButton.querySelector("svg").outerHTML = iconSvg(icon);
      }
      if (state.best) upgradeText.textContent = "Улучшенное качество разделения";
      else if (state.status === "queued") upgradeText.textContent = `Улучшение в очереди${left}`;
      else if (state.status === "running") upgradeText.textContent = `Улучшаем · ${Math.round(state.progress * 100)}%${left}`;
      else upgradeText.textContent = `Улучшить качество${state.estimate_seconds ? ` · ${formatMinutes(state.estimate_seconds)}` : ""}`;
    };
    const followUpgrade = async (state) => {
      while (state.status === "queued" || state.status === "running") {
        showUpgrade(state);
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        if (setupId !== playbackSetupId) return;
        state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/upgrade`);
        if (setupId !== playbackSetupId) return;
      }
      showUpgrade(state);
      if (state.status === "failed") error.textContent = state.error || "Не удалось улучшить качество";
      if (state.status === "done" && state.best) {
        pendingResume = activeControls ? activeControls.snapshot() : null;
        await openProject(projectId);  // new stem files: the player is rebuilt on them
      }
    };
    const followSafely = (state) => followUpgrade(state).catch((reason) => {
      error.textContent = reason.message;
      upgradeButton.disabled = false;
    });
    upgradeButton.addEventListener("click", async () => {
      upgradeButton.disabled = true;
      try {
        await followSafely(await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/upgrade`, { method: "POST" }));
      } catch (reason) {
        error.textContent = reason.message;
        upgradeButton.disabled = false;
      }
    });
    const upgrade = project.upgrade || { status: "none", best: false };
    showUpgrade(upgrade);
    if (upgrade.status === "queued" || upgrade.status === "running") followSafely(upgrade);

    footer.append(metronomeButton, bpmLabel, volumeWrap, upgradeButton, upgradeCancel, speedWrap);

    const setMetronome = (on) => {
      metronomeOn = on;
      metronomeButton.setAttribute("aria-pressed", String(on));
      if (!on) metronome.reset();
    };
    // Beats are detected on the first use only (it takes about half a minute), with progress on the button.
    let beatsRequest = null;
    const loadBeats = () => {
      beatsRequest = beatsRequest || (async () => {
        let state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/beats`, { method: "POST" });
        while (state.status === "queued" || state.status === "running") {
          if (setupId !== playbackSetupId) return null;
          metronomeButton.dataset.analyzing = "true";
          metronomeButton.style.setProperty("--job-progress", `${Math.round(state.progress * 100)}%`);
          nameMetronome(`Метроном · ${Math.round(state.progress * 100)}%`);
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/beats`);
        }
        delete metronomeButton.dataset.analyzing;
        metronomeButton.style.removeProperty("--job-progress");
        nameMetronome("Метроном");
        if (state.status !== "done") throw new Error(state.error || "Не удалось определить доли");
        if (!rhythmFound(state.result)) {
          setMetronome(false);
          nameMetronome("Ритм не найден");
          noRhythm = true;
          showTempo(transport.currentTime);
          metronomeButton.disabled = volume.disabled = countIn.disabled = true;
          return null;
        }
        metronome.setBeats(state.result.beats);
        songTempo = state.result.tempo && state.result.tempo.length ? state.result.tempo : [[0, state.result.bpm]];
        showTempo(transport.currentTime);
        return state.result.beats;
      })().catch((reason) => {
        beatsRequest = null;
        delete metronomeButton.dataset.analyzing;
        metronomeButton.style.removeProperty("--job-progress");
        nameMetronome("Метроном");
        error.textContent = reason.message;
        setMetronome(false);
        return null;
      });
      return beatsRequest;
    };
    countIn.addEventListener("click", async () => {
      if (countInTimer !== null || transport.playing) {
        await togglePlayback();  // a second press stops, like ▶
        return;
      }
      if (decodedReady) await decodedReady;  // the clicks are timed for a start that will not wait
      const beats = metronome.beats.length ? metronome.beats : await loadBeats();
      if (!beats || setupId !== playbackSetupId) return;
      await audioContext.resume();
      const plan = countInPlan(beats, transport.currentTime, audioContext.currentTime + 0.05, 4, transport.rate);
      if (!plan) return;
      plan.clicks.forEach((when) => metronome.click(when));
      metronomeButton.dataset.clicks = String(metronome.clicks);
      countIn.setAttribute("aria-pressed", "true");
      // Without the metronome only these four clicks sound; with it, it carries on from the song's beats.
      countInTimer = window.setTimeout(async () => {
        countInTimer = null;
        countIn.removeAttribute("aria-pressed");
        await togglePlayback();
      }, Math.max(0, (plan.audioStart - audioContext.currentTime) * 1000));
    });
    metronomeButton.addEventListener("click", async () => {
      if (metronomeOn) {
        setMetronome(false);
        return;
      }
      setMetronome(true);
      await loadBeats();
    });
    let positioned = false;
    tracks.forEach((audio) => audio.addEventListener("loadedmetadata", () => {
      if (transport.duration > 0) {
        duration = transport.duration;
        playheadLayer.setAttribute("aria-valuemax", duration.toFixed(1));
        // Once all stems know their length, move them past the leading silence.
        if (!positioned) {
          positioned = true;
          const resume = pendingResume && pendingResume.projectId === projectId ? pendingResume : null;
          pendingResume = null;
          transport.seek(resume ? resume.position : 0);
          if (resume) restore(resume);
        } else {
          updateTime(transport.currentTime);
        }
      }
    }));
    detailControls.replaceChildren(masterRow);
    detailTransport.replaceChildren(buttons);
    playheadLayer.style.gridRow = `1 / span ${stems.length}`;
    mixerRows.append(playheadLayer);
    if (original) playbackPanel.replaceChildren(error, mixerRows, ...tracks);  // metronome, upgrade, speed: with the stems
    else playbackPanel.replaceChildren(error, mixerRows, footer, ...tracks);
    if (decodedNote.textContent) mixerRows.after(decodedNote);
    playbackPanel.hidden = false;
    selectStem(0);
    // After an upgrade the new player picks up where the old one was.
    const restore = async (resume) => {
      if (resume.speed !== speed.value) {
        speed.value = resume.speed;
        await applySpeed();
      }
      if (resume.metronome) {
        setMetronome(true);
        await loadBeats();
      }
      if (resume.playing && setupId === playbackSetupId) await togglePlayback();
    };
    activeControls = {
      snapshot: () => ({ projectId, position: transport.currentTime, playing: transport.playing, speed: speed.value, metronome: metronomeOn }),
      togglePlayback,
      seekBy: (seconds) => transport.seek(transport.currentTime + seconds),
      seekTo: (seconds) => transport.seek(seconds),
      moveSelection: (step) => selectStem(selectedStem + step),
      cycleSelection: (step) => selectStem((selectedStem + step + stems.length) % stems.length),
      selectStemByName: (name) => {
        const index = stems.findIndex((asset) => asset.stem_name === name);
        if (index >= 0) selectStem(index);
      },
      press: (control) => mixerRowElements[selectedStem].querySelector(`[data-control="${control}"]`).click(),
      toStartAndPause: () => reset.click(),
      nudgeGain: (step) => {  // through the slider's own input handler: the same 0-150 % range and 100 % snap
        const gain = mixerRowElements[selectedStem].querySelector('[data-control="gain"]');
        gain.value = String(Number(gain.value) + step);
        gain.dispatchEvent(new Event("input"));
      },
    };
    tracks.forEach((audio) => audio.load());
    if (original && finishedStates.has(project.job.status)) renderSeparateAgain(project);
    else if (original) renderJobProgress(selectedJobState || { job_status: project.job.status, progress: 0 });
  } catch (reason) {
    console.error("Playback setup failed", reason);
    heading.textContent = "Не удалось подготовить синхронное воспроизведение в этом браузере.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
  }
}

let openingProjectId = null;  // the last tab asked for: a slower answer for an earlier one is dropped
async function openProject(projectId) {
  if (projectId !== selectedProjectId) cleanupPlayback();
  openingProjectId = projectId;
  try {
    const project = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`);
    if (openingProjectId !== projectId) return;
    renderProject(project);
    renderProjects(await requestJson("/api/v1/projects"));
  } catch (error) {
    detailError.textContent = error.message;
    detailError.hidden = false;
  }
}

async function loadProjects(selectJobId = null) {
  try {
    const projects = await requestJson("/api/v1/projects");
    renderProjects(projects);
    if (selectJobId) {
      const match = projects.find((project) => project.job_id === selectJobId);
      if (match) await openProject(match.project_id);
    } else if (!selectedProjectId && projects.length) {
      await openProject(projects[0].project_id);
    } else if (selectedProjectId) {
      const selected = projects.find((project) => project.project_id === selectedProjectId);
      if (!selected) {
        selectedProjectId = null;
        projectDetail.hidden = true;
        projectEmpty.hidden = false;
      }
    }
  } catch (error) {
    projectTabs.replaceChildren();
    const message = document.createElement("p");
    message.className = "tabs-empty";
    message.textContent = error.message;
    projectTabs.append(message);
  }
}

async function refreshBatch(batchId, selectedJobId) {
  try {
    const batch = await requestJson(`/api/v1/batches/${encodeURIComponent(batchId)}`);
    await loadProjects();
    if (!batchActiveStates.has(batch.status)) {
      const matching = [...projectTabs.querySelectorAll(".project-tab")].find((tab) => tab.dataset.jobId === selectedJobId);
      // Show the finished project only if the user has not moved on to another one meanwhile.
      if (matching && (!selectedProjectId || selectedProjectId === matching.dataset.projectId)) {
        await openProject(matching.dataset.projectId);
      }
      pollingTimer = null;
      return;
    }
    pollingTimer = window.setTimeout(() => refreshBatch(batchId, selectedJobId), 1200);
  } catch (_error) {
    pollingTimer = window.setTimeout(() => refreshBatch(batchId, selectedJobId), 2500);
  }
}

// Settings → Обновления: the releases newer than this copy; the newest can be installed.
function renderReleases(result) {
  const releases = result.releases;
  if (releases.length === 0) {
    releaseList.replaceChildren();
    updatesMessage.textContent = `Установлена версия ${result.current} — это последняя.`;
    return;
  }
  updatesMessage.textContent = `Установлена версия ${result.current}. Есть новая:`;
  releaseList.replaceChildren(...releases.map((release, index) => {
    const card = document.createElement("article");
    card.className = "release-card";
    const title = document.createElement("h3");
    title.textContent = release.name || release.tag_name;
    const published = document.createElement("p");
    published.textContent = new Date(release.published_at).toLocaleDateString("ru-RU");
    const notes = document.createElement("p");
    notes.className = "release-notes";
    notes.textContent = release.notes || "В этом релизе нет описания.";
    const link = document.createElement("a");
    link.href = release.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Открыть на GitHub";
    card.append(title, published, notes, link);
    if (index === 0) {
      const apply = document.createElement("button");
      apply.type = "button";
      apply.className = "primary-button";
      apply.dataset.action = "install-update";
      apply.textContent = `Обновить до ${release.tag_name}`;
      apply.addEventListener("click", async () => {
        if (!(await askConfirm({ title: `Установить «${release.name || release.tag_name}»?`, text: "Задачи в очереди должны закончиться. Ваши песни и настройки не затрагиваются. После установки приложение нужно запустить заново.", action: "Установить", danger: false }))) return;
        apply.disabled = true;
        updatesMessage.textContent = "Скачиваем обновление…";
        try {
          const done = await requestJson("/api/v1/updates/apply", {
            method: "POST",
            body: JSON.stringify({ tag_name: release.tag_name, confirmed: true }),
          });
          updatesMessage.textContent = done.state === "applied"
            ? "Обновление установлено. Закройте окно Audio Stems и запустите приложение снова."
            : "Обновление скачано. Закройте окно Audio Stems и запустите приложение снова: оно установится при запуске.";
        } catch (error) {
          updatesMessage.textContent = error.message;
          apply.disabled = false;
        }
      });
      card.append(apply);
    }
    return card;
  }));
}

async function checkUpdates() {
  checkUpdatesButton.disabled = true;
  updatesMessage.textContent = "Проверяем новые версии на GitHub…";
  try {
    renderReleases(await requestJson("/api/v1/updates"));
  } catch (error) {
    updatesMessage.textContent = error.message;
  } finally {
    checkUpdatesButton.disabled = false;
  }
}

// The separation model is chosen in Settings and remembered in this browser.
async function loadModels() {
  let saved = "";
  try { saved = localStorage.getItem("audio-stems-model") || ""; } catch (_error) { /* storage blocked: use the default */ }
  try {
    const models = await requestJson("/api/v1/models");
    selectedModel = (models.find((model) => model.name === saved) || models.find((model) => model.default) || models[0] || {}).name || "";
    modelOptions.replaceChildren(...models.map((model) => {
      const option = document.createElement("label");
      option.className = "model-option";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "model";
      radio.value = model.name;
      radio.checked = model.name === selectedModel;
      radio.addEventListener("change", () => {
        selectedModel = model.name;
        storeSetting("audio-stems-model", model.name);
      });
      const name = document.createElement("span");
      name.className = "model-option-name";
      name.textContent = `${model.label || model.name} · ${model.stems.length} дорожек`.replace("4 дорожек", "4 дорожки");
      const note = document.createElement("span");
      note.className = "model-option-note";
      note.textContent = model.description || "";
      if (model.seconds_per_audio_second) {
        const time = document.createElement("span");
        time.className = "model-option-time";
        time.textContent = `${perMinuteOfSong(model.seconds_per_audio_second)} на минуту песни · ${model.measured
          ? "по последним разделениям на этом компьютере" : "оценка, уточнится после первого разделения"}`;
        note.append(document.createElement("br"), time);
      }
      option.append(radio, name, note);
      return option;
    }));
  } catch (error) {
    modelOptions.textContent = `Не удалось загрузить модели: ${error.message}`;
  }
}

async function submitSearch(event) {
  event.preventDefault();
  if (pollingTimer !== null) window.clearTimeout(pollingTimer);
  const inputs = inputField.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (inputs.length === 0) {
    setFormMessage("Введите название песни или ссылку.", true);
    return;
  }
  submitButton.disabled = true;
  setFormMessage("Добавляем в очередь…");
  try {
    const created = await requestJson("/api/v1/batches", {
      method: "POST",
      body: JSON.stringify(selectedModel ? { inputs, model_name: selectedModel } : { inputs }),
    });
    inputField.value = "";
    inputField.style.height = "";
    closeSearch({ restoreFocus: false });
    await loadProjects(created.job_ids[0]);
    pollingTimer = window.setTimeout(() => refreshBatch(created.batch_id, created.job_ids[0]), 1200);
  } catch (error) {
    // The API names the problem with a link in English; the only place the dialog mentions YouTube.
    setFormMessage(/URL/.test(error.message) ? "Ссылка не подходит: поддерживаются ссылки YouTube (youtube.com, youtu.be)." : error.message, true);
  } finally {
    submitButton.disabled = false;
  }
}

async function uploadFiles(files) {
  if (files.length === 0) return;
  openSearch();
  if (pollingTimer !== null) window.clearTimeout(pollingTimer);
  submitButton.disabled = openFileButton.disabled = true;
  let created = null;
  try {
    for (const file of files) {
      setFormMessage(`Загружаем «${file.name}»…`);
      const query = selectedModel ? `?model_name=${encodeURIComponent(selectedModel)}` : "";
      // The body is the file itself (no form encoding); the server reads its name from X-Filename.
      const response = await fetch(`/api/v1/uploads${query}`, {
        method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(`«${file.name}»: ${body.error?.message || "не удалось загрузить"}`);
      created = body;
    }
    setFormMessage("");
    closeSearch({ restoreFocus: false });
  } catch (error) {
    setFormMessage(error.message, true);
  } finally {
    submitButton.disabled = openFileButton.disabled = false;
    fileInput.value = "";
  }
  if (created) {
    await loadProjects(created.job_ids[0]);
    pollingTimer = window.setTimeout(() => refreshBatch(created.batch_id, created.job_ids[0]), 1200);
  }
}

function formatBytes(bytes) {
  if (bytes >= 2 ** 30) return `${(bytes / 2 ** 30).toFixed(1).replace(".", ",")} ГБ`;
  return `${Math.max(bytes ? 1 : 0, Math.round(bytes / 2 ** 20))} МБ`;
}

// The app's own confirmation (not window.confirm): resolves true when the action's button is pressed, false on
// «Отмена» or Esc. With run, the dialog stays open while it works and shows its error.
let confirmPending = null;
function askConfirm({ title, text, action, danger = true, run = null }) {
  confirmPending?.resolve(false);
  document.querySelector("#confirm-title").textContent = title;
  document.querySelector("#confirm-description").textContent = text;
  confirmButton.textContent = action;
  confirmButton.className = danger ? "danger-action" : "primary-button";
  confirmError.hidden = true;
  if (!confirmDialog.open) confirmDialog.showModal();
  return new Promise((resolve) => { confirmPending = { resolve, run }; });
}

confirmButton.addEventListener("click", async () => {
  if (!confirmPending) return;
  const pending = confirmPending;
  if (pending.run) {
    confirmButton.disabled = true;
    confirmError.hidden = true;
    try {
      await pending.run();
    } catch (error) {
      confirmError.textContent = error.message;
      confirmError.hidden = false;
      return;
    } finally {
      confirmButton.disabled = false;
    }
  }
  confirmPending = null;
  confirmDialog.close("confirmed");
  pending.resolve(true);
});
confirmDialog.addEventListener("close", () => {
  confirmPending?.resolve(false);
  confirmPending = null;
});

async function deleteProject(projectId) {
  // Stop the stems first: Windows cannot delete a file the browser is still reading.
  if (projectId === selectedProjectId) cleanupPlayback();
  try {
    await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
  } catch (error) {
    if (projectId === selectedProjectId) openProject(projectId);  // the project stays: bring its player back
    throw error;
  }
  if (projectId === selectedProjectId) {
    selectedProjectId = null;
    selectedProjectTitle = "";
  }
  await loadProjects();
}

// Appearance: theme and accent hue live on <html>; index.html re-applies the saved values on load.
function storeSetting(key, value) {
  try { localStorage.setItem(key, value); } catch (_error) { /* private mode: setting lasts this page only */ }
}

function applyTheme(theme) {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  themeButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme)));
  themeColorMeta.content = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
}

themeButtons.forEach((button) => button.addEventListener("click", () => {
  applyTheme(button.dataset.themeChoice);
  storeSetting("audio-stems-theme", button.dataset.themeChoice);
}));
accentHueField.addEventListener("input", () => {
  document.documentElement.style.setProperty("--accent-h", accentHueField.value);
  storeSetting("audio-stems-accent-hue", accentHueField.value);
});
accentHueField.value = getComputedStyle(document.documentElement).getPropertyValue("--accent-h").trim() || "197";
applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");

searchTrigger.addEventListener("click", openSearch);
document.querySelector("#close-search").addEventListener("click", () => closeSearch());
searchOverlay.addEventListener("click", (event) => {
  if (event.target === searchOverlay) closeSearch();
});
document.querySelector("#close-settings").addEventListener("click", closeSettings);
document.querySelector("#help-trigger").addEventListener("click", openHelp);
queueTrigger.addEventListener("click", () => (queuePopover.hidden ? openQueue() : closeQueue()));
tabsTrigger.addEventListener("click", () => (tabsPopover.hidden ? openTabList() : closeTabList()));
document.addEventListener("click", (event) => {
  if (!queuePopover.hidden && !queuePopover.contains(event.target) && !queueTrigger.contains(event.target)) closeQueue();
  if (!tabsPopover.hidden && !tabsPopover.contains(event.target) && !tabsTrigger.contains(event.target) && !confirmDialog.contains(event.target)) closeTabList();
});
document.querySelector("#close-help").addEventListener("click", closeHelp);
helpOverlay.addEventListener("click", (event) => {
  if (event.target === helpOverlay) closeHelp();
});
settingsOverlay.addEventListener("click", (event) => {
  if (event.target === settingsOverlay) closeSettings();
});
function isTextEntry(target) {
  if (!(target instanceof Element)) return false;
  if (target.isContentEditable || target.matches("textarea, select")) return true;
  return target.matches("input") && !["range", "checkbox", "radio", "button", "submit"].includes(target.type);
}

// Hotkeys work whenever a project is open and no text field has focus; event.code keeps
// S/M working on any keyboard layout.
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.code === "KeyF") {
    event.preventDefault();
    openSearch();
    return;
  }
  if (event.key === "Escape" && !searchOverlay.hidden) {
    closeSearch();
    return;
  }
  if (event.key === "Escape" && !settingsOverlay.hidden) {
    closeSettings();
    return;
  }
  if (event.key === "Escape" && !queuePopover.hidden) {
    closeQueue();
    return;
  }
  if (event.key === "Escape" && !tabsPopover.hidden && !confirmDialog.open) {
    closeTabList();
    return;
  }
  if (event.key === "Escape" && !helpOverlay.hidden) {
    closeHelp();
    return;
  }
  // Tab never walks the focus (and its focus rings) outside dialogs; in a project it picks the next track.
  if (event.key === "Tab" && searchOverlay.hidden && settingsOverlay.hidden && helpOverlay.hidden && !confirmDialog.open && !isTextEntry(event.target)) {
    event.preventDefault();
    if (activeControls && !event.ctrlKey && !event.metaKey && !event.altKey) {
      activeControls.cycleSelection(event.shiftKey ? -1 : 1);
    }
    return;
  }
  // Tabs, also while a project has no player yet: 1-9 open the first nine, Q and W the previous and next (by key
  // position, so any layout works), round the ends.
  if (searchOverlay.hidden && settingsOverlay.hidden && helpOverlay.hidden && !confirmDialog.open
      && !event.ctrlKey && !event.metaKey && !event.altKey && !isTextEntry(event.target)) {
    const tabs = [...projectTabs.querySelectorAll(".project-tab")];
    const digit = /^(?:Digit|Numpad)([1-9])$/.exec(event.code);
    let target = null;
    if (digit) {
      target = tabs[Number(digit[1]) - 1];
    } else if ((event.code === "KeyQ" || event.code === "KeyW") && tabs.length) {
      const step = event.code === "KeyW" ? 1 : -1;
      // From the tab last asked for: the project may still be loading (and a poll redraws the tabs meanwhile).
      const current = tabs.findIndex((tab) => tab.dataset.projectId === (openingProjectId ?? selectedProjectId));
      target = tabs[current < 0 ? (step > 0 ? 0 : tabs.length - 1) : (current + step + tabs.length) % tabs.length];
    }
    if (digit || target) {
      event.preventDefault();
      if (target && target.dataset.projectId !== (openingProjectId ?? selectedProjectId)) {
        target.scrollIntoView({ block: "nearest", inline: "nearest" });
        openProject(target.dataset.projectId);
      }
      return;
    }
  }
  if (!activeControls || !searchOverlay.hidden || !settingsOverlay.hidden || !helpOverlay.hidden || confirmDialog.open) return;
  if (event.ctrlKey || event.metaKey || event.altKey || isTextEntry(event.target)) return;
  const controls = activeControls;
  const actions = {
    ArrowLeft: () => controls.seekBy(-5),
    ArrowRight: () => controls.seekBy(5),
    Home: () => controls.seekTo(0),
    Backspace: () => controls.toStartAndPause(),  // the ⏮ button: back to the start, paused
    ArrowUp: () => controls.moveSelection(-1),
    ArrowDown: () => controls.moveSelection(1),
    Space: () => controls.togglePlayback(),
    Enter: () => controls.togglePlayback(),
    NumpadEnter: () => controls.togglePlayback(),
    KeyS: () => controls.press("solo"),
    KeyM: () => controls.press("mute"),
    KeyV: () => controls.selectStemByName("vocals"),
    KeyD: () => controls.selectStemByName("drums"),
    KeyB: () => controls.selectStemByName("bass"),
    KeyG: () => controls.selectStemByName("guitar"),
    KeyP: () => controls.selectStemByName("piano"),
    KeyO: () => controls.selectStemByName("other"),
    // The «+» and «−» keys, by position, so any layout works: =/+ and -/_ on the main keys, and the numpad.
    Equal: () => controls.nudgeGain(10),
    NumpadAdd: () => controls.nudgeGain(10),
    Minus: () => controls.nudgeGain(-10),
    NumpadSubtract: () => controls.nudgeGain(-10),
  };
  const action = actions[event.code];
  if (action) {
    // preventDefault stops a focused button or slider from also reacting to the key.
    event.preventDefault();
    const toggles = ["Space", "Enter", "NumpadEnter", "KeyS", "KeyM"].includes(event.code);
    if (!(toggles && event.repeat)) action();
  } else if (event.target instanceof Element && event.target.matches('[data-control="gain"]')) {
    event.preventDefault(); // PageUp/PageDown must not change volume either
  }
});
document.querySelectorAll("[data-screen]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    activateScreen(link.dataset.screen);
  });
});
form.addEventListener("submit", submitSearch);
inputField.addEventListener("keydown", (event) => {
  // Enter starts the search, Shift+Enter adds a line; Enter that confirms an IME composition is left alone.
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});
openFileButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => uploadFiles(Array.from(fileInput.files)));
let dragDepth = 0;  // dragenter/dragleave fire for every child element crossed
const draggingFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
window.addEventListener("dragenter", (event) => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  dropOverlay.hidden = false;
});
window.addEventListener("dragover", (event) => {
  if (draggingFiles(event)) event.preventDefault();  // allows the drop
});
window.addEventListener("dragleave", (event) => {
  if (!draggingFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropOverlay.hidden = true;
});
window.addEventListener("drop", (event) => {
  if (!draggingFiles(event)) return;
  event.preventDefault();  // otherwise the browser opens the file itself
  dragDepth = 0;
  dropOverlay.hidden = true;
  uploadFiles(Array.from(event.dataTransfer.files));
});
inputField.addEventListener("input", () => {
  inputField.style.height = "auto";
  inputField.style.height = `${Math.min(inputField.scrollHeight, window.innerHeight * 0.4)}px`;
});
deleteProjectButton.addEventListener("click", () => {
  const projectId = deleteProjectButton.dataset.projectId;
  if (projectId) askDeleteProject(projectId, selectedProjectTitle, selectedProjectBytes);
});
document.querySelector("#confirm-cancel").addEventListener("click", () => confirmDialog.close("cancel"));
checkUpdatesButton.addEventListener("click", checkUpdates);
cleanStorageButton.addEventListener("click", cleanStorage);
deleteAllButton.addEventListener("click", deleteAllProjects);

loadProjects();
loadModels();
refreshQueue();
if (location.hash === "#settings") openSettings();
