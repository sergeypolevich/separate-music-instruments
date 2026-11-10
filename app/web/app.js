const stateDescriptions = {
  queued: "Queued",
  searching: "Finding the source",
  downloading: "Downloading audio",
  downloaded: "Audio downloaded",
  separating: "Separating stems",
  completed: "Done",
  failed: "Processing failed",
  cancelled: "Task cancelled",
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
// The name the user gave, else the source's title, else what was typed.
const projectName = (project) => project.name || project.title || project.input_value;
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

// Icons are drawn in the same 24px grid as the static SVG icons in index.html (lucide style, ISC). The vocal, bass and
// guitar icons are the user's own (1024 units), the drum and the hare are drawn after the user's pictures; piano (four keys of
// it) and other are Delapouite's from game-icons.net (CC BY 3.0, see THIRD_PARTY.md). All are scaled to the grid.
const transportIcons = {
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  start: '<path d="M6 5v14"/><path d="M18 5.5v13L8.5 12z" fill="currentColor" stroke="none"/>',
  metronome: '<path d="M9 3h6l4 18H5z"/><path d="M12 16l5-9"/><path d="M7 15h10"/>',
  countin: '<text x="6.5" y="17" font-size="14" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">4</text><path d="M12.5 6.5v11l8.5-5.5z" fill="currentColor" stroke="none"/>',
  vocals: '<g transform="scale(0.031562) translate(-122.8 -73)" fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M356 220C356 259.218 371.357 294.848 396.386 321.206L609.453 321.375L609.5 321.325C634.596 294.956 650 259.277 650 220C650 138.814 584.186 73 503 73C421.814 73 356 138.814 356 220ZM353.5 226.5V204.5H652.5V226.5H353.5Z"/><g opacity=".8"><path d="M412.0 485.0C421.7 623.3 427.2 742.5 430.4 833.4L575.9 833.4C579.0 740.5 584.6 619.6 594.5 485.0Z"/><path d="M594.5 477.5L412 478L404.5 385.5H405.5V351H601.5V386H602.5L594.5 477.5Z"/><path d="M353.5 226.5V204.5H652.5V226.5H353.5Z"/></g></g>',
  drums: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M16 150A240 60 0 0 1 496 150V380A240 60 0 0 1 16 380ZM40 150a216 44 0 1 0 432 0a216 44 0 1 0 -432 0zM16 180A240 60 0 0 0 496 180V190A240 60 0 0 1 16 190ZM16 340A240 60 0 0 0 496 340V350A240 60 0 0 1 16 350ZM109 238H115V277H109ZM109 349H115V388H109ZM108 277h8a12 12 0 0 1 12 12v48a12 12 0 0 1 -12 12h-8a12 12 0 0 1 -12 -12v-48a12 12 0 0 1 12 -12zM110 283h4a8 8 0 0 1 8 8v44a8 8 0 0 1 -8 8h-4a8 8 0 0 1 -8 -8v-44a8 8 0 0 1 8 -8zM205 248.8H211V287.8H205ZM205 359.8H211V398.8H205ZM204 287.8h8a12 12 0 0 1 12 12v48a12 12 0 0 1 -12 12h-8a12 12 0 0 1 -12 -12v-48a12 12 0 0 1 12 -12zM206 293.8h4a8 8 0 0 1 8 8v44a8 8 0 0 1 -8 8h-4a8 8 0 0 1 -8 -8v-44a8 8 0 0 1 8 -8zM301 248.8H307V287.8H301ZM301 359.8H307V398.8H301ZM300 287.8h8a12 12 0 0 1 12 12v48a12 12 0 0 1 -12 12h-8a12 12 0 0 1 -12 -12v-48a12 12 0 0 1 12 -12zM302 293.8h4a8 8 0 0 1 8 8v44a8 8 0 0 1 -8 8h-4a8 8 0 0 1 -8 -8v-44a8 8 0 0 1 8 -8zM397 238H403V277H397ZM397 349H403V388H397ZM396 277h8a12 12 0 0 1 12 12v48a12 12 0 0 1 -12 12h-8a12 12 0 0 1 -12 -12v-48a12 12 0 0 1 12 -12zM398 283h4a8 8 0 0 1 8 8v44a8 8 0 0 1 -8 8h-4a8 8 0 0 1 -8 -8v-44a8 8 0 0 1 8 -8z"/></g>',
  bass: '<g transform="scale(.0234375)" fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M302.386 827.433C302.386 824.233 388.786 408.231 434.653 157.029C480.52 -94.1726 863.456 92.4952 634.655 307.963C585.588 418.364 706.655 534.631 700.255 767.166C635.188 786.366 590.388 833.3 590.388 907.434C590.921 939.434 590.388 969.301 590.388 969.301H428.787C418.12 874.9 294.386 928.767 302.386 827.433ZM378.1191 809.3a39.4669 39.4669 0 1 0 78.9338 0a39.4669 39.4669 0 1 0 -78.9338 0zM416.5201 617.298a39.4669 39.4669 0 1 0 78.9338 0a39.4669 39.4669 0 1 0 -78.9338 0zM455.9871 425.297a39.4669 39.4669 0 1 0 78.9338 0a39.4669 39.4669 0 1 0 -78.9338 0zM495.45410000000004 232.229a39.4669 39.4669 0 1 0 78.9338 0a39.4669 39.4669 0 1 0 -78.9338 0z"/><path d="M187.185 760.766C160.518 743.166 158.385 694.632 204.252 681.832C221.319 638.099 283.186 639.165 284.786 694.632C284.786 694.632 282.119 720.766 315.719 731.433L314.652 735.166L320.519 736.766L314.652 765.033L307.186 762.899L305.586 766.633C276.786 757.033 266.652 785.833 266.652 785.833C240.519 833.3 180.252 809.833 187.185 760.766Z"/><path d="M225.343 572.547C198.677 554.947 196.543 506.413 242.41 493.613C259.477 449.88 321.344 450.946 322.944 506.413C322.944 506.413 320.277 532.547 353.877 543.214L352.811 546.947L358.678 548.547L352.811 576.814L345.344 574.68L343.744 578.414C314.944 568.814 304.811 597.614 304.811 597.614C278.677 645.081 218.41 621.614 225.343 572.547Z"/><path d="M263.744 377.346C237.077 359.746 234.943 311.212 280.81 298.412C297.877 254.678 359.744 255.745 361.344 311.212C361.344 311.212 358.678 337.346 392.278 348.012L391.211 351.746L397.32 353.297L391.986 381.564L383.744 379.479L382.144 383.212C353.344 373.612 343.211 402.412 343.211 402.412C317.077 449.88 256.81 426.413 263.744 377.346Z"/><path d="M301.077 186.411C274.41 168.811 272.277 120.277 318.144 107.477C335.211 63.7438 397.078 64.8105 398.678 120.278C398.678 120.278 396.011 146.411 429.611 157.078L428.545 160.811L434.411 162.411L428.545 190.678L421.078 188.545L419.478 192.278C390.678 182.678 380.544 211.478 380.544 211.478C354.411 258.945 294.144 235.478 301.077 186.411Z"/><path d="M430.387 985.301h160.001v38.699h-160.001z"/></g>',
  guitar: '<g transform="scale(.0234375)" fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M336.5 816.5C336.5 816.5 343.158 793.782 354.382 755.5C356 749.983 357.713 744.142 359.514 738C399.165 602.781 481.753 321.279 540.5 122C590 -4.49998 825.5 35 783 230.5C765.623 288.5 680.5 307.5 680.5 323.5C659.5 355 733.5 570 749 736C708.5 765.5 629.187 791.5 618 921.5C616.677 936.869 618 961 618 961H408C408 961 408.365 953.172 408 948.5C407.257 939 392 907.643 354 880.5C340 870.5 326.391 861.731 336.5 816.5ZM388.0 770.0a19.0 19.0 0 1 0 38.0 0a19.0 19.0 0 1 0 -38.0 0zM422.0 654.0a19.0 19.0 0 1 0 38.0 0a19.0 19.0 0 1 0 -38.0 0zM456.0 538.0a19.0 19.0 0 1 0 38.0 0a19.0 19.0 0 1 0 -38.0 0zM490.0 423.0a19.0 19.0 0 1 0 38.0 0a19.0 19.0 0 1 0 -38.0 0zM524.0 306.0a19.0 19.0 0 1 0 38.0 0a19.0 19.0 0 1 0 -38.0 0zM558.0 190.0a19.0 19.0 0 1 0 38.0 0a19.0 19.0 0 1 0 -38.0 0z"/><path d="M407 1023.5V977.5H618.5V1023.5H407Z"/><path d="M332 749C341 751.5 354.382 755.5 354.382 755.5L359.514 738L336.5 732C340 704 325.5 697 313 694.5C294.5 690.8 279 704 274.5 725C269.022 750.566 278.869 765.955 297.5 769.5C316 774 327 760 332 749Z"/><path d="M366.172 632.5C375.172 635 388.554 639 388.554 639L393.686 621.5L370.672 615.5C374.172 587.5 359.672 580.5 347.172 578C328.672 574.3 313.172 587.5 308.672 608.5C303.194 634.066 313.041 649.455 331.672 653C350.172 657.5 361.172 643.5 366.172 632.5Z"/><path d="M399.923 517.5C408.923 520 422.306 524 422.306 524L427.437 506.5L404.423 500.5C407.923 472.5 393.423 465.5 380.923 463C362.423 459.3 346.923 472.5 342.423 493.5C336.945 519.066 346.792 534.455 365.423 538C383.923 542.5 394.923 528.5 399.923 517.5Z"/><path d="M433.551 403C442.551 405.5 455.933 409.5 455.933 409.5L461.064 392L438.051 386C441.551 358 427.051 351 414.551 348.5C396.051 344.8 380.551 358 376.051 379C370.572 404.566 380.419 419.955 399.051 423.5C417.551 428 428.551 414 433.551 403Z"/><path d="M467.192 288.5C476.192 291 489.574 295 489.574 295L494.706 277.5L471.692 271.5C475.192 243.5 460.692 236.5 448.192 234C429.692 230.3 414.192 243.5 409.692 264.5C404.214 290.066 414.061 305.455 432.692 309C451.192 313.5 462.192 299.5 467.192 288.5Z"/><path d="M501.494 172C510.494 174.5 523.876 178.5 523.876 178.5L529.008 161L505.994 155C509.494 127 494.994 120 482.494 117.5C463.994 113.8 448.494 127 443.994 148C438.516 173.566 448.363 188.955 466.994 192.5C485.494 197 496.494 183 501.494 172Z"/></g>',
  piano: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M104 80v352h64V288h-16V80h-48zm96 0v208h-16v144h64V288h-16V80h-32zm80 0v208h-16v144h64V288h-16V80h-32zm80 0v208h-16v144h64V80h-48z"/></g>',
  other: '<g transform="scale(.046875)" fill="currentColor" stroke="none"><path d="M385.853 21.083c-2.876-.097-4.956.287-6.693 1.058 4.438 38.628-3.264 69.792-21.635 99.469-19.234 31.07-49.316 60.967-88.852 100.502l-3.586 3.722 21.079 21.079 3.722-3.586c28.35-28.35 51.736-51.808 74.16-69.922a50.053 50.053 0 0 1 7.662-.918 47.688 47.688 0 0 1 2.582-.033c12.806.18 23.989 5.554 32.149 13.714 16.225 16.225 21.424 44.398-3.31 73.522-8.9-7.421-17.504-10.952-25.503-10.69a24.831 24.831 0 0 0-4.064.47c-10.692 2.138-17.328 9.532-22.984 15.189l-23.334 23.334 12.726 12.728 23.336-23.336c5.657-5.657 10.335-9.575 13.785-10.265a9.39 9.39 0 0 1 2.133-.188c2.622.075 6.14 1.307 11.451 5.711l-39.39 39.39 12.728 12.73 45.961-45.962c36.549-36.548 33.505-81.048 9.193-105.36-8.559-8.558-19.627-14.465-31.962-16.952 1.06-.683 2.12-1.356 3.183-2.014 29.677-18.371 60.841-26.073 99.469-21.635.771-1.737 1.155-3.817 1.058-6.693-.16-4.804-1.931-11.321-5.304-18.549-6.746-14.455-19.647-31.608-34.625-46.586-14.978-14.978-32.131-27.88-46.586-34.625-7.228-3.373-13.745-5.143-18.549-5.304zM217.492 202.584l-15.557 15.557 12.729 12.727 15.556-15.555-12.728-12.729zm23.486 24.596-14.143 14.143 111.723 111.722 14.143-14.14L240.978 227.18zm-50.309 2.227-15.556 15.556 12.728 12.727 15.557-15.555-12.729-12.728zm23.438 24.644-14.143 14.143L311.69 379.916l14.14-14.142L214.108 254.05zm-50.203 2.121-15.557 15.557 12.729 12.729L176.63 268.9l-12.726-12.729zm23.334 24.748-14.143 14.143 111.723 111.723 14.142-14.141L187.238 280.92zm-13.75 39.99L51.355 447.683l12.963 12.963 39.988-38.525c.004.092.001.186.006.279.877 17.378 8.833 33.331 20.701 45.2 11.868 11.867 27.822 19.822 45.2 20.698 17.377.877 35.967-5.7 51.673-21.406l48.791-48.79-12.728-12.73-42.414 42.415c-7.028-7.302-6.922-10.348-6-13.532.964-3.332 5.28-8.376 10.937-14.033l26.162-26.162-12.726-12.728-26.164 26.164c-5.657 5.656-12.655 11.926-15.5 21.755-2.715 9.379.623 20.584 10.172 30.83-10.453 7.99-21.092 10.756-31.297 10.24-12.41-.625-24.387-6.459-33.38-15.45-8.99-8.992-14.824-20.968-15.45-33.377-.334-6.622.726-13.428 3.681-20.246l65.117-62.735-17.6-17.601zM33.796 455.578l-12.728 12.728 22.627 22.627 12.728-12.728-22.627-22.627z"/></g>',
  // Filled, the inner lines cut out. shortcut: fixed mask ids, fine while one speed block is on the page; number
  // them if the snail or the hare is shown twice.
  snail: '<mask id="snail-cut" stroke="none"><rect width="24" height="24" fill="#fff"/><path d="M2 12a6 6 0 1 0 12 0 4 4 0 1 0-8 0 2 2 0 0 0 4 0" stroke="#000" stroke-width="1.2"/></mask><mask id="snail-gap" stroke="none"><rect width="24" height="24" fill="#fff"/><circle cx="10" cy="12" r="9.1" fill="#000"/></mask><g fill="currentColor" stroke="none"><circle cx="10" cy="12" r="8" mask="url(#snail-cut)"/><path mask="url(#snail-gap)" d="M2 20.2h12c3.5 0 4.6-3.2 4.6-7.2V7a1.9 1.9 0 0 1 3.8 0v6c0 5-3.8 9-8.4 9H2a.9.9 0 0 1 0-1.8z"/></g><path d="M19.7 5.4 18.8 2.6M21.3 5.4l.9-2.8" stroke-width="1.3"/>',
  rabbit: '<path fill="currentColor" stroke="none" fill-rule="evenodd" d="M17.1 2.9C16.9 2.8 16.9 2.9 16.9 2.9C16.8 2.9 16.7 2.8 16.7 3.1C16.7 3.3 17.1 4.4 16.8 4.6C16.6 4.7 15.5 4.2 15.2 4.1C14.8 4.0 14.8 4.1 14.7 4.1C14.6 4.1 14.6 4.1 14.6 4.2C14.6 4.3 14.5 4.4 14.6 4.6C14.6 4.8 14.8 5.1 14.9 5.4C15.0 5.6 15.2 5.8 15.4 6.0C15.5 6.2 15.7 6.4 16.1 6.6C16.4 6.8 17.1 7.1 17.4 7.2C17.7 7.4 17.9 7.5 18.0 7.6C18.1 7.7 18.1 7.7 18.0 7.8C18.0 7.8 17.9 7.9 17.8 7.9C17.7 8.0 17.4 8.0 17.3 8.0C17.1 8.1 17.2 8.1 16.7 8.0C16.2 7.9 14.9 7.6 14.1 7.4C13.3 7.3 12.6 7.1 12.0 7.0C11.3 7.0 10.7 7.0 10.1 7.0C9.6 7.1 9.3 7.2 9.0 7.3C8.6 7.4 8.3 7.5 8.0 7.6C7.6 7.8 7.3 7.9 6.9 8.2C6.5 8.4 5.8 9.0 5.5 9.1C5.3 9.3 5.5 9.2 5.4 9.1C5.4 9.0 5.3 8.8 5.3 8.7C5.2 8.7 5.1 8.6 5.0 8.6C4.9 8.6 4.7 8.6 4.5 8.7C4.4 8.7 4.2 8.8 4.1 9.0C3.9 9.1 3.8 9.3 3.7 9.5C3.6 9.6 3.6 9.8 3.5 10.0C3.5 10.1 3.4 10.2 3.4 10.4C3.4 10.6 3.3 11.0 3.3 11.2C3.3 11.4 3.3 11.5 3.5 11.8C3.7 12.0 4.2 12.5 4.3 12.7C4.5 12.9 4.4 12.9 4.4 13.0C4.4 13.2 4.4 13.2 4.4 13.4C4.3 13.6 4.3 13.7 4.0 14.1C3.7 14.6 2.8 15.5 2.5 15.8C2.2 16.2 2.4 15.8 2.1 16.5C1.8 17.2 0.8 19.4 0.6 20.1C0.3 20.8 0.5 20.5 0.5 20.6C0.5 20.8 0.6 21.0 0.7 21.0C0.8 21.1 0.9 21.1 1.0 21.1C1.1 21.1 1.1 21.4 1.5 20.8C1.9 20.2 3.0 18.2 3.4 17.6C3.8 17.0 3.4 17.3 3.9 17.1C4.4 16.8 5.5 16.5 6.3 16.0C7.0 15.5 8.0 14.6 8.5 14.2C9.0 13.8 9.0 13.8 9.3 13.7C9.6 13.5 9.8 13.4 10.1 13.3C10.4 13.2 10.6 13.1 10.9 13.0C11.2 13.0 11.5 12.9 11.8 12.9C12.2 12.9 12.7 12.9 13.0 12.9C13.4 12.9 13.1 12.8 13.9 13.0C14.7 13.2 16.6 13.8 17.8 14.3C18.9 14.7 20.4 15.6 21.0 15.9C21.6 16.2 21.3 16.0 21.5 16.0C21.6 15.9 21.8 15.8 21.9 15.8C22.0 15.7 22.0 15.6 22.0 15.4C22.1 15.3 22.0 15.1 22.0 14.9C22.0 14.8 22.0 14.8 21.9 14.7C21.9 14.7 21.9 14.6 21.7 14.5C21.4 14.4 20.9 14.2 20.6 14.1C20.3 13.9 20.2 13.8 19.9 13.6C19.7 13.5 19.5 13.2 19.3 13.0C19.2 12.8 19.2 12.8 19.2 12.6C19.3 12.4 19.5 12.3 19.8 12.0C20.1 11.7 20.8 11.0 21.0 10.8C21.3 10.6 21.1 10.8 21.3 10.7C21.6 10.7 22.2 10.8 22.5 10.8C22.8 10.7 22.8 10.7 22.9 10.6C23.0 10.5 23.1 10.4 23.2 10.3C23.3 10.3 23.3 10.2 23.4 10.1C23.4 9.9 23.5 9.8 23.5 9.6C23.5 9.5 23.5 9.4 23.5 9.2C23.4 9.0 23.4 8.7 23.3 8.4C23.1 8.2 23.0 7.9 22.8 7.6C22.7 7.3 22.4 7.0 22.1 6.8C21.9 6.5 21.8 6.5 21.5 6.3C21.2 6.2 20.8 6.2 20.4 5.9C20.0 5.5 19.5 4.6 19.1 4.2C18.8 3.8 18.6 3.7 18.2 3.5C17.9 3.3 17.3 3.0 17.1 2.9ZM22.88 9.20a0.48 0.48 0 1 0 -0.95 0a0.48 0.48 0 1 0 0.95 0Z"/>',
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
  if (!response.ok) throw new Error(body.error?.message || "The request failed");
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
    const rows = [["Projects", report.projects_bytes], ["Files without a project", report.unused_bytes],
      ["Temporary files", report.temp_bytes], ["Separation models", report.models_bytes]];
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
    deleteAllButton.textContent = projectsBytes ? `Delete all projects (${formatBytes(projectsBytes)})` : "Delete all projects";
    cleanStorageButton.textContent = cleanableBytes ? `Clean up ${formatBytes(cleanableBytes)}` : "Clean up";
    diskMessage.textContent = report.busy ? "Wait until the separation ends: cleaning up and deleting are unavailable while it runs." : "";
  } catch (error) {
    diskMessage.textContent = error.message;
  }
}

async function cleanStorage() {
  const confirmed = await askConfirm({ title: "Clean up storage?", text: `Files without a project and temporary files (${formatBytes(cleanableBytes)}) will be deleted. Projects stay as they are.`, action: "Delete" });
  if (!confirmed) return;
  cleanStorageButton.disabled = true;
  try {
    const result = await requestJson("/api/v1/storage/cleanup", { method: "POST", body: JSON.stringify({ confirmed: true }) });
    await loadStorage();
    diskMessage.textContent = `Freed ${formatBytes(result.freed_bytes)}.`;
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
    diskMessage.textContent = "No projects.";
    return;
  }
  const freed = projectsBytes;
  if (!(await askConfirm({ title: `Delete all projects (${projects.length})?`, text: `With their audio and stems: ${formatBytes(freed)}. This cannot be undone.`, action: "Delete all" }))) return;
  deleteAllButton.disabled = cleanStorageButton.disabled = true;
  cleanupPlayback();  // Windows cannot delete a file the browser is still reading
  const failed = [];
  for (const project of projects) {
    try {
      await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}`, { method: "DELETE" });
    } catch (_error) {
      failed.push(projectName(project));
    }
  }
  selectedProjectId = null;
  selectedProjectTitle = "";
  await loadProjects();
  await loadStorage();
  diskMessage.textContent = failed.length ? `Could not delete: ${failed.join(", ")}.` : `Projects deleted: ${projects.length}. Freed ${formatBytes(freed)}.`;
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
  ["Play / pause", "Space, Enter"],
  ["Back / forward 5 seconds", "←, →"],
  ["To the start", "Home"],
  ["To the start and pause", "Backspace"],
  ["Next / previous stem, in a loop", "Tab, Shift+Tab"],
  ["Open tab 1–9", "1–9"],
  ["Tab: previous / next", "Q, W"],
  ["Select Vocal, Drums, Bass, Guitar, Piano, Other", "V, D, B, G, P, O"],
  ["Solo the selected stem", "S"],
  ["Mute the selected stem", "M"],
  ["Selected stem volume +10% / −10%", "+, −"],
  ["Master volume +5% / −5%", "↑, ↓"],
  ["Open a file or find a song", /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘F" : "Ctrl+F"],
  ["New line in the search field", "Shift+Enter"],
  ["Clear loop", "Esc"],
  ["Close the window", "Esc"],
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
  const left = item.eta_seconds !== null && item.eta_seconds !== undefined ? ` · ${formatMinutes(item.eta_seconds)} left` : "";
  if (item.kind === "upgrade") return `Quality improvement${item.status === "queued" ? " · queued" : ""}${left}`;
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
  const label = items.length === 1 ? "Queue: 1 task" : `Queue: ${items.length} tasks`;
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
    cancel.setAttribute("aria-label", `Cancel: ${item.title}`);
    cancel.title = "Cancel";
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
  const what = item.kind === "upgrade" ? `the quality improvement of “${item.title}”` : `“${item.title}”`;
  if (workingStates.has(item.status) && !(await askConfirm({ title: `Cancel ${what}?`, text: "What is already done will be lost.", action: "Cancel task" }))) return;
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
  const nameText = projectName(project);
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

// Separation time for one minute of music: "~55 s" or "~11 min".
function perMinuteOfSong(secondsPerSecond) {
  const seconds = secondsPerSecond * 60;
  return seconds < 60 ? `~${Math.max(5, Math.round(seconds / 5) * 5)} s` : `~${Math.round(seconds / 60)} min`;
}

function formatMinutes(seconds) {
  return seconds < 60 ? "less than a minute" : `~${Math.round(seconds / 60)} min`;
}

function etaText(state) {
  if (state.eta_seconds === null || state.eta_seconds === undefined) return "";
  return state.job_status === "separating"
    ? ` · ${formatMinutes(state.eta_seconds)} left`
    : ` · separation will take ${formatMinutes(state.eta_seconds)}`;
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
    cancel.setAttribute("aria-label", "Cancel");
    cancel.title = "Cancel";
    setIcon(cancel, "close");
    cancel.addEventListener("click", () => cancelTask({
      kind: "job", job_id: selectedJobId, title: selectedProjectTitle, status: selectedJobState?.job_status || "searching" }));
    // The stage over the bar, ✕ right of it; under the tracks (the original audio plays meanwhile), alone before them.
    const status = document.createElement("div");
    status.className = "job-status";
    status.append(label, bar, cancel);
    playbackPanel.querySelector(".separate-again")?.remove();  // «Separate» was pressed: progress again
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
  if (reordering) return;  // the next poll (or the drop's reload) draws them
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
    empty.textContent = "Projects will appear here";
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
    button.addEventListener("click", () => {
      // On a phone (no title to edit) a second tap on the same tab within 400 ms renames it.
      const now = performance.now();
      const twice = lastTabTap.id === project.project_id && now - lastTabTap.time < 400 && phoneLayout.matches;
      lastTabTap = { id: project.project_id, time: twice ? 0 : now };
      if (twice) renameInTab(button);
      else openProject(project.project_id);
    });
    return button;
  }));
  updateTabFades();
}

let lastTabTap = { id: null, time: 0 };
const phoneLayout = matchMedia("(max-width: 600px)");  // as in app.css, where the title is hidden

// Renaming: in the title on a computer, in a field over the tab after a double tap. Leaving the text saves it,
// Enter too, Esc keeps the old name; an empty name shows the source's title again.
async function renameProject(projectId, text) {
  try {
    await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH", body: JSON.stringify({ name: text.replace(/\s+/g, " ").trim().slice(0, 200) }),
    });
  } catch (error) {
    detailError.textContent = error.message;
    detailError.hidden = false;
  }
  if (projectId === selectedProjectId) await openProject(projectId);  // the title, the tabs and the list
  else await loadProjects();
}

function editName(field, read, cancel, save) {
  const before = read();
  let cancelled = false;
  const onKey = (event) => {
    if (event.key === "Enter") event.preventDefault();
    if (event.key === "Escape") cancelled = true;
    if (event.key === "Enter" || event.key === "Escape") field.blur();
  };
  field.addEventListener("keydown", onKey);
  field.addEventListener("blur", () => {
    field.removeEventListener("keydown", onKey);
    if (cancelled) cancel(before);
    else if (read() !== before) save(read());
  }, { once: true });
}

detailTitle.addEventListener("focus", () => {
  const projectId = selectedProjectId;
  editName(detailTitle, () => detailTitle.textContent, (before) => { detailTitle.textContent = before; },
    (text) => renameProject(projectId, text));
});

function renameInTab(button) {
  const box = button.getBoundingClientRect();
  const field = document.createElement("input");
  field.className = "tab-rename";
  field.value = button.firstElementChild.textContent;
  field.maxLength = 200;
  field.setAttribute("aria-label", "Project name");
  Object.assign(field.style, { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` });
  document.body.append(field);
  editName(field, () => field.value, () => {}, (text) => renameProject(button.dataset.projectId, text));
  field.addEventListener("blur", () => field.remove(), { once: true });
  field.focus();
  field.select();
}

// The list of every project (the button right of the tabs): a click opens one, ✕ deletes it after a question;
// one still being worked on is cancelled first.
function renderTabList(projects) {
  tabsTrigger.hidden = !projects.length;
  if (!projects.length) closeTabList();
  document.querySelector("#tabs-list").replaceChildren(...projects.map((project) => {
    const nameText = projectName(project);
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
    remove.setAttribute("aria-label", `Delete: ${nameText}`);
    remove.title = "Delete";
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
      title: "Cancel and delete the project?",
      text: `“${nameText}” is still processing: the processing will stop, the project and its files will be deleted.`,
      action: "Cancel and delete",
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
    ? ` ${formatBytes(bytes.shared)} of shared files stay: another project uses them.` : "";
  askConfirm({
    title: "Delete the project?",
    text: `The project “${nameText}”, its source audio and all stems will be deleted. This frees ${formatBytes(bytes.disk)}.${shared}`,
    action: "Delete",
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
    throw new Error("The processing is still stopping. Try deleting the project a little later.");
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
  tabsPopover.style.maxHeight = `${innerHeight - tabsPopover.getBoundingClientRect().top - 12}px`;  // down to the window's bottom
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
  if (reordering) {  // the tab was held and lifted: it moves, the strip does not
    tabDrag = null;
    return;
  }
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

// Drag to reorder: the tabs sideways, the project list up and down. The item follows the pointer, the others make
// room, and on release the new order is saved. hold(event): the pointer must first rest 300 ms without moving
// (where a move scrolls: the tab strip, and the list by touch); otherwise a 5 px move starts the drag.
let reordering = false;  // while an item is lifted the polls leave the tabs and the list alone

function reorderable(container, scroller, selector, axis, hold) {
  const along = (event) => (axis === "x" ? event.clientX : event.clientY);
  const scrolled = () => (axis === "x" ? scroller.scrollLeft : scroller.scrollTop);
  const translate = (offset) => (axis === "x" ? `translateX(${offset}px)` : `translateY(${offset}px)`);
  let drag = null;
  let suppressClick = false;
  const place = () => {
    const [low, high] = drag.edges[drag.from];
    const [first, last] = [drag.edges[0][0], drag.edges[drag.edges.length - 1][1]];
    const offset = Math.min(Math.max(drag.at + scrolled() - drag.start, first - low), last - high);  // within the row
    const centre = (low + high) / 2 + offset;
    drag.to = drag.from;
    drag.edges.forEach(([a, b], index) => {
      if (index > drag.from && centre >= (a + b) / 2) drag.to = index;  // >=: the row's ends are reachable
      if (index < drag.from && centre <= (a + b) / 2 && drag.to === drag.from) drag.to = index;
    });
    drag.items.forEach((item, index) => {
      const room = index > drag.from && index <= drag.to ? -drag.size : index < drag.from && index >= drag.to ? drag.size : 0;
      item.style.transform = item === drag.item ? `${translate(offset)} scale(1.04)` : room ? translate(room) : "";
    });
  };
  const autoScroll = () => {  // near an edge the list scrolls on by itself, so an item can go past what is visible
    if (!drag?.lifted) return;
    const box = scroller.getBoundingClientRect();
    const [low, high] = axis === "x" ? [box.left, box.right] : [box.top, box.bottom];
    const step = drag.at < low + 32 ? -8 : drag.at > high - 32 ? 8 : 0;
    if (step) {
      if (axis === "x") scroller.scrollLeft += step;
      else scroller.scrollTop += step;
      place();
    }
    requestAnimationFrame(autoScroll);
  };
  const lift = () => {
    window.clearTimeout(drag.timer);
    drag.items = [...container.querySelectorAll(selector)];
    drag.edges = drag.items.map((item) => {  // in the content's coordinates, so scrolling moves the item along
      const box = item.getBoundingClientRect();
      return axis === "x" ? [box.left + scrolled(), box.right + scrolled()] : [box.top + scrolled(), box.bottom + scrolled()];
    });
    drag.from = drag.items.indexOf(drag.item);
    const [low, high] = drag.edges[drag.from];
    const next = drag.edges[drag.from + 1];
    const previous = drag.edges[drag.from - 1];
    drag.size = high - low + (next ? next[0] - high : previous ? low - previous[1] : 0);  // its length and the gap
    drag.lifted = reordering = true;
    container.setPointerCapture(drag.pointer);
    container.classList.add("reordering");
    drag.item.classList.add("lifted");
    place();
    autoScroll();
  };
  const end = (event) => {
    if (!drag || event.pointerId !== drag.pointer) return;
    window.clearTimeout(drag.timer);
    const { lifted, items, from, to, item, x, y } = drag;
    drag = null;
    if (!lifted) return;
    reordering = false;
    container.classList.remove("reordering");  // first: the items jump into place instead of sliding back
    item.classList.remove("lifted");
    items.forEach((other) => { other.style.transform = ""; });
    if (Math.hypot(event.clientX - x, event.clientY - y) > 5) {  // a slow click still opens the tab
      suppressClick = true;  // the release after a drag is not a click on the item
      window.setTimeout(() => { suppressClick = false; }, 0);
    }
    if (event.type === "pointercancel" || to === from) return;
    container.insertBefore(item, to > from ? items[to].nextSibling : items[to]);
    saveProjectOrder([...container.querySelectorAll(selector)].map((other) => other.dataset.projectId));
  };
  container.addEventListener("pointerdown", (event) => {
    const item = event.target.closest(selector);
    if (!item || event.button !== 0 || event.target.closest("[data-action]")) return;
    drag = { item, pointer: event.pointerId, x: event.clientX, y: event.clientY, at: along(event), start: along(event) + scrolled(), lifted: false, timer: null };
    if (hold(event)) drag.timer = window.setTimeout(lift, 300);
  });
  container.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointer) return;
    drag.at = along(event);
    if (drag.lifted) place();
    else if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 5) {
      if (drag.timer) {  // moved before the hold ended: a scroll, not a drag
        window.clearTimeout(drag.timer);
        drag = null;
      } else lift();
    }
  });
  container.addEventListener("pointerup", end);
  container.addEventListener("pointercancel", end);
  container.addEventListener("touchmove", (event) => { if (drag?.lifted) event.preventDefault(); }, { passive: false });
  container.addEventListener("contextmenu", (event) => { if (drag) event.preventDefault(); });  // a long press
  container.addEventListener("click", (event) => {
    if (!suppressClick) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);
}

async function saveProjectOrder(projectIds) {
  try {
    await requestJson("/api/v1/projects/order", { method: "PUT", body: JSON.stringify({ project_ids: projectIds }) });
  } catch (error) {  // the list changed meanwhile (409): the reload shows the server's order
    detailError.textContent = error.message;
    detailError.hidden = false;
  }
  loadProjects();
}

reorderable(projectTabs, projectTabs, ".project-tab", "x", () => true);
reorderable(document.querySelector("#tabs-list"), tabsPopover, ".tabs-item", "y", (event) => event.pointerType !== "mouse");

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
    svg.setAttribute("aria-label", "Waveform unavailable");
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
  const names = missing.map((asset) => (asset.asset_type === "stem" ? STEM_LABELS[asset.stem_name] || asset.stem_name : "source audio"));
  message.textContent = `Files of the project were deleted from the disk: ${names.join(", ")}.`;
  if (project.input_type === "local_file") {  // nothing to download again: the user has the file
    message.textContent += " Delete the project and open the file again.";
    playbackPanel.replaceChildren(message);
    playbackPanel.hidden = false;
    return;
  }
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "primary-button";
  retry.textContent = "Separate again";
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
  note.textContent = project.job.status === "cancelled" ? "Separation cancelled." : "The separation failed.";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "primary-button";
  button.dataset.action = "separate";
  button.textContent = "Separate";
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
  selectedProjectTitle = projectName(project);
  selectedJobId = project.job.job_id;
  projectEmpty.hidden = true;
  projectDetail.hidden = false;
  if (document.activeElement !== detailTitle) detailTitle.textContent = selectedProjectTitle;  // a poll never undoes typing
  detailTitle.title = selectedProjectTitle;
  markClippedTitle();
  detailError.textContent = project.job.error_summary || "";
  detailError.hidden = !project.job.error_summary;
  deleteProjectButton.dataset.projectId = project.project_id;
  selectedProjectBytes = { disk: project.disk_bytes || 0, shared: project.shared_bytes || 0 };
  detailSize.textContent = formatBytes(selectedProjectBytes.disk + selectedProjectBytes.shared);
  detailSize.title = "Disk space: source audio and stems";
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
      // Stopped before its stems: listen to the song and separate it again («Separate»).
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
    heading.textContent = "Synchronized playback needs all stems of the project.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
    return;
  }

  try {
    const { BufferEngine, MixerSession, SynchronizedTransport, Metronome, PlayheadClock, StretchEngine, countInPlan, snapLoop, formatBpm, metronomeGain, rhythmFound, snapFaderPercent, tempoAt } = await import("./playback.mjs");
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
    // «Master»: every stem (streamed or stretched, both end in its gain node) passes it; the metronome does not.
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
    playheadLayer.setAttribute("aria-label", "Playback position");
    playheadLayer.setAttribute("aria-valuemin", "0");
    playheadLayer.setAttribute("aria-valuemax", "0");
    playheadLayer.setAttribute("aria-valuenow", "0");
    const playhead = document.createElement("span");
    playhead.className = "playhead";
    // The loop: a light band over every stem between its ends (song time).
    const loopRegion = document.createElement("span");
    loopRegion.className = "loop-region";
    loopRegion.hidden = true;
    playheadLayer.append(loopRegion, playhead);
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
    play.setAttribute("aria-label", "Play");
    play.title = "Play";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "transport-button";
    reset.dataset.action = "reset";
    setIcon(reset, "start");
    reset.setAttribute("aria-label", "To the start and pause");
    reset.title = "To the start and pause";
    const countIn = document.createElement("button");
    countIn.type = "button";
    countIn.className = "transport-button";
    countIn.dataset.action = "count-in";
    setIcon(countIn, "countin");
    countIn.setAttribute("aria-label", "Count-in and play");
    countIn.title = "Count-in and play";
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
      play.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
      play.title = isPlaying ? "Pause" : "Play";
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
      const text = noRhythm ? "No beat found" : formatBpm(tempoAt(songTempo, seconds), metronome.rate);
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
      onLoopChange: (loop) => {
        metronome.loop = loop;
        metronome.reset();  // clicks scheduled past the old loop's end
        showLoop(loop);
      },
      beforePlay: () => audioContext.resume(),
      startOffset,
    });
    activeTransport = transport;
    const showLoop = (loop) => {
      loopRegion.hidden = !loop || !(duration > 0);
      if (loopRegion.hidden) return;
      loopRegion.style.left = `${(loop.start / duration) * 100}%`;
      loopRegion.style.width = `${((loop.end - loop.start) / duration) * 100}%`;
      loopRegion.dataset.start = String(loop.start);
      loopRegion.dataset.end = String(loop.end);
    };
    const timeAt = (event) => {
      const rect = playheadLayer.getBoundingClientRect();
      return Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)) * duration;
    };
    // A drag over the waveforms makes a loop, its ends on the nearest beats. The stems play it decoded (gapless,
    // sample-locked), prepared on the first loop; beats not detected yet are detected then and the loop snaps.
    let loopRequest = 0;
    const makeLoop = async (from, to) => {
      const request = ++loopRequest;
      showLoop(snapLoop(from, to, metronome.beats));
      try { await loadStretch({ plain: true }); } catch (_reason) { showLoop(transport.loop); return; }
      if (request !== loopRequest || setupId !== playbackSetupId) return;
      await transport.setLoop(snapLoop(from, to, metronome.beats));
      if (metronome.beats.length || noRhythm) return;
      const beats = await loadBeats();
      if (beats && request === loopRequest && transport.loop) await transport.setLoop(snapLoop(from, to, beats));
    };
    // The layer covers the whole waveform column, gaps included: a click anywhere there seeks (out of a loop:
    // drops it), a drag of more than 4 px makes a loop, a double click on the loop drops it.
    let loopDrag = null;
    playheadLayer.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !(duration > 0)) return;
      playheadLayer.setPointerCapture(event.pointerId);
      loopRequest += 1;  // a loop still being prepared is forgotten
      loopDrag = { x: event.clientX, from: timeAt(event), moved: false };
      transport.seek(loopDrag.from);
    });
    playheadLayer.addEventListener("pointermove", (event) => {
      if (!loopDrag || !playheadLayer.hasPointerCapture(event.pointerId)) return;
      if (!loopDrag.moved && Math.abs(event.clientX - loopDrag.x) < 4) return;
      loopDrag.moved = true;
      const to = timeAt(event);
      showLoop({ start: Math.min(loopDrag.from, to), end: Math.max(loopDrag.from, to) });
    });
    playheadLayer.addEventListener("pointerup", (event) => {
      if (loopDrag && loopDrag.moved) makeLoop(loopDrag.from, timeAt(event));
      loopDrag = null;
    });
    playheadLayer.addEventListener("pointercancel", () => {
      loopDrag = null;
      showLoop(transport.loop);
    });
    playheadLayer.addEventListener("dblclick", (event) => {
      if (transport.inLoop(timeAt(event))) transport.setLoop(null, transport.currentTime);
    });

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
      gain.setAttribute("aria-label", `${stemLabels[asset.stem_name]} volume`);
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
      waveSvg.setAttribute("aria-label", `Waveform: ${stemLabels[asset.stem_name]}`);
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
    masterName.innerHTML = iconSvg("speaker");  // the fader carries the name «Master volume»
    const masterFader = document.createElement("div");
    masterFader.className = "mixer-fader master-fader";
    const master = document.createElement("input");
    master.type = "range";
    master.min = "0";
    master.max = "100";
    master.step = "1";
    master.dataset.control = "master-volume";
    master.tabIndex = -1;
    master.setAttribute("aria-label", "Master volume");
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
        error.textContent = "The browser did not allow all stems to start. Press ▶ again.";
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
    nameMetronome("Metronome");
    const volumeWrap = document.createElement("div");
    volumeWrap.className = "mixer-fader footer-fader";
    const volume = document.createElement("input");
    volume.type = "range";
    volume.min = "0";
    volume.max = "100";
    volume.step = "1";
    volume.dataset.control = "metronome-volume";
    volume.tabIndex = -1;
    volume.setAttribute("aria-label", "Metronome volume");
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
    slower.innerHTML = iconSvg("snail");
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
    speed.setAttribute("aria-label", "Playback speed");
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
    // plain: without AudioWorklet (a page over http from another device) the decoded mode plays at 100 % only.
    const loadStretch = ({ plain = false } = {}) => {
      stretchLoading = stretchLoading || (async () => {
        if (!audioContext.audioWorklet && !plain) throw new Error("over http from another device the browser does not allow it, only on this computer or over https");
        // Three steps per stem (downloaded, decoded, handed to its node), shown as a fill on the block.
        let steps = 0;
        const step = () => {
          steps += 1;
          const percent = Math.round((steps / (3 * tracks.length)) * 100);
          speedWrap.style.setProperty("--job-progress", `${percent}%`);
          speedValue.textContent = `preparing ${percent}%`;
        };
        speedWrap.dataset.loading = "true";
        speedWrap.style.setProperty("--job-progress", "0%");
        speedValue.textContent = "preparing 0%";
        const decoded = await Promise.all(tracks.map(async (audio) => {
          const response = await fetch(audio.currentSrc || audio.src);
          const data = await response.arrayBuffer();
          step();
          const buffer = await audioContext.decodeAudioData(data);
          step();
          return buffer;
        }));
        if (audioContext.audioWorklet) {
          const { default: createStretch } = await import("./vendor/signalsmith-stretch-1.3.2.mjs");
          transport.stretch = await StretchEngine.create(audioContext, createStretch, decoded, gainNodes, step);
        } else {
          transport.stretch = new BufferEngine(audioContext, decoded, gainNodes);
          speed.disabled = true;
          speedWrap.title = "Speed change: only on this computer or over https";
        }
        delete speedWrap.dataset.loading;
        speedWrap.style.removeProperty("--job-progress");
      })().catch((reason) => {
        stretchLoading = null;
        delete speedWrap.dataset.loading;
        speedWrap.style.removeProperty("--job-progress");
        error.textContent = `Could not turn on the speed change: ${reason.message}`;
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

    // Decoded playback on iPhone and iPad (Safari resumes streamed stems one after another, so they drift apart
    // after a pause; iPadOS reports itself as a Mac with touch) or with ?engine=decoded anywhere: the decoded
    // stems through the stretch engine at every speed, all on the AudioContext clock. Prepared at once; ▶ and ▶4
    // wait for it; if it fails the stems stream as usual. Only the trial parameter keeps the "ready in N s" note.
    const decodedNote = document.createElement("p");
    decodedNote.className = "playback-message decoded-note";
    let decodedReady = null;
    const decodedTrial = new URLSearchParams(location.search).get("engine") === "decoded";
    const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (decodedTrial || apple) {
      const started = performance.now();
      decodedNote.textContent = decodedTrial ? "Decoded mode: preparing…" : "Preparing stems…";
      decodedReady = loadStretch({ plain: true }).then(() => {
        if (setupId !== playbackSetupId) return;
        transport.alwaysStretch = true;
        if (!transport.playing) transport.seek(transport.currentTime);  // the engine takes the paused position
        speedWrap.dataset.engine = "stretch";
        if (decodedTrial) decodedNote.textContent = `Decoded mode: ready in ${((performance.now() - started) / 1000).toFixed(1)} s`;
        else {
          decodedNote.textContent = "";  // the panel may be rebuilt later: an empty note is not put back
          decodedNote.remove();
        }
      }).catch(() => {
        decodedReady = null;
        decodedNote.textContent = "Decoded mode did not start: the stems play as usual.";
      });
    }

    // «Improve quality»: the worker separates the song again with the best model while this project
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
    upgradeCancel.setAttribute("aria-label", "Cancel the improvement");
    upgradeCancel.title = "Cancel the improvement";
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
      const left = state.eta_seconds !== null && state.eta_seconds !== undefined ? ` · ${formatMinutes(state.eta_seconds)} left` : "";
      const icon = state.best ? "check" : "sparkles";  // done: a check mark instead of the sparkles
      if (upgradeButton.dataset.icon !== icon) {
        upgradeButton.dataset.icon = icon;
        upgradeButton.querySelector("svg").outerHTML = iconSvg(icon);
      }
      if (state.best) upgradeText.textContent = "Improved separation quality";
      else if (state.status === "queued") upgradeText.textContent = `Improvement queued${left}`;
      else if (state.status === "running") upgradeText.textContent = `Improving · ${Math.round(state.progress * 100)}%${left}`;
      else upgradeText.textContent = `Improve quality${state.estimate_seconds ? ` · ${formatMinutes(state.estimate_seconds)}` : ""}`;
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
      if (state.status === "failed") error.textContent = state.error || "Could not improve the quality";
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
          nameMetronome(`Metronome · ${Math.round(state.progress * 100)}%`);
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/beats`);
        }
        delete metronomeButton.dataset.analyzing;
        metronomeButton.style.removeProperty("--job-progress");
        nameMetronome("Metronome");
        if (state.status !== "done") throw new Error(state.error || "Could not find the beats");
        if (!rhythmFound(state.result)) {
          setMetronome(false);
          nameMetronome("No beat found");
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
        nameMetronome("Metronome");
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
      seekTo: (seconds) => (transport.loop ? transport.setLoop(null, seconds) : transport.seek(seconds)),  // Home drops a loop
      dropLoop: () => { if (transport.loop) transport.setLoop(null, transport.currentTime); },
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
      nudgeMaster: (step) => {  // the slider clamps to 0-100 % and its input handler stores the value
        master.value = String(Number(master.value) + step);
        master.dispatchEvent(new Event("input"));
      },
    };
    tracks.forEach((audio) => audio.load());
    if (original && finishedStates.has(project.job.status)) renderSeparateAgain(project);
    else if (original) renderJobProgress(selectedJobState || { job_status: project.job.status, progress: 0 });
  } catch (reason) {
    console.error("Playback setup failed", reason);
    heading.textContent = "Could not prepare synchronized playback in this browser.";
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

// Settings → Updates: the releases newer than this copy; the newest can be installed.
function renderReleases(result) {
  const releases = result.releases;
  if (releases.length === 0) {
    releaseList.replaceChildren();
    updatesMessage.textContent = `Version ${result.current} is installed. It is the latest.`;
    return;
  }
  updatesMessage.textContent = `Version ${result.current} is installed. A new one is available:`;
  releaseList.replaceChildren(...releases.map((release, index) => {
    const card = document.createElement("article");
    card.className = "release-card";
    const title = document.createElement("h3");
    title.textContent = release.name || release.tag_name;
    const published = document.createElement("p");
    published.textContent = new Date(release.published_at).toLocaleDateString("en-US", { dateStyle: "medium" });
    const notes = document.createElement("p");
    notes.className = "release-notes";
    notes.textContent = release.notes || "This release has no description.";
    const link = document.createElement("a");
    link.href = release.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Open on GitHub";
    card.append(title, published, notes, link);
    if (index === 0) {
      const apply = document.createElement("button");
      apply.type = "button";
      apply.className = "primary-button";
      apply.dataset.action = "install-update";
      apply.textContent = `Update to ${release.tag_name}`;
      apply.addEventListener("click", async () => {
        if (!(await askConfirm({ title: `Install “${release.name || release.tag_name}”?`, text: "The app will restart by itself. A running download or separation continues after the restart (a separation starts over). Your songs and settings are not affected.", action: "Install", danger: false }))) return;
        // The button itself shows each step: a message above the card went unnoticed (a user's report).
        apply.disabled = true;
        apply.setAttribute("aria-busy", "true");
        apply.textContent = "Downloading…";
        updatesMessage.textContent = "Downloading the update…";
        try {
          const before = (await requestJson("/api/v1/health")).process;
          await requestJson("/api/v1/updates/apply", {
            method: "POST",
            body: JSON.stringify({ tag_name: release.tag_name, confirmed: true }),
          });
          apply.textContent = "Restarting…";
          updatesMessage.textContent = "The app is restarting with the new version. If it needs new libraries, this takes a few minutes: the progress shows in the Audio Stems window.";
          await waitForRestart(before);
          window.location.reload();
        } catch (error) {
          updatesMessage.textContent = error.message;
          apply.textContent = `Update to ${release.tag_name}`;
          apply.disabled = false;
          apply.removeAttribute("aria-busy");
        }
      });
      card.append(apply);
    }
    return card;
  }));
}

// After an update the server answers again under a new process number: the new version is running.
async function waitForRestart(before) {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    try {
      const response = await fetch("/api/v1/health", { cache: "no-store" });
      if (response.ok && (await response.json()).process !== before) return;
    } catch (_error) {
      // still restarting
    }
  }
}

async function checkUpdates() {
  checkUpdatesButton.disabled = true;
  updatesMessage.textContent = "Checking GitHub for new versions…";
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
      name.textContent = `${model.label || model.name} · ${model.stems.length} stems`;
      const note = document.createElement("span");
      note.className = "model-option-note";
      note.textContent = model.description || "";
      if (model.seconds_per_audio_second) {
        const time = document.createElement("span");
        time.className = "model-option-time";
        time.textContent = `${perMinuteOfSong(model.seconds_per_audio_second)} per minute of song · ${model.measured
          ? "from the latest separations on this computer" : "an estimate, refined after the first separation"}`;
        note.append(document.createElement("br"), time);
      }
      option.append(radio, name, note);
      return option;
    }));
  } catch (error) {
    modelOptions.textContent = `Could not load the models: ${error.message}`;
  }
}

async function submitSearch(event) {
  event.preventDefault();
  if (pollingTimer !== null) window.clearTimeout(pollingTimer);
  const inputs = inputField.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (inputs.length === 0) {
    setFormMessage("Enter a song title or a link.", true);
    return;
  }
  submitButton.disabled = true;
  setFormMessage("Adding to the queue…");
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
    setFormMessage(/URL/.test(error.message) ? "This link does not fit: YouTube links are supported (youtube.com, youtu.be)." : error.message, true);
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
      setFormMessage(`Uploading “${file.name}”…`);
      const query = selectedModel ? `?model_name=${encodeURIComponent(selectedModel)}` : "";
      // The body is the file itself (no form encoding); the server reads its name from X-Filename.
      const response = await fetch(`/api/v1/uploads${query}`, {
        method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(`“${file.name}”: ${body.error?.message || "could not upload"}`);
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
  if (bytes >= 2 ** 30) return `${(bytes / 2 ** 30).toFixed(1)} GB`;
  return `${Math.max(bytes ? 1 : 0, Math.round(bytes / 2 ** 20))} MB`;
}

// The app's own confirmation (not window.confirm): resolves true when the action's button is pressed, false on
// «Cancel» or Esc. With run, the dialog stays open while it works and shows its error.
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
    Escape: () => controls.dropLoop(),  // with no window open (those close first, above)
    Backspace: () => controls.toStartAndPause(),  // the ⏮ button: back to the start, paused
    ArrowUp: () => controls.nudgeMaster(5),
    ArrowDown: () => controls.nudgeMaster(-5),
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
