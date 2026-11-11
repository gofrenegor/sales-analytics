const state = {
  mode: "Месяц",
  anchor: "",
  rangeStart: "",
  rangeEnd: "",
  today: "",
  boundYears: [],
  view: "dashboard",
  flow: "",
  group: "",
  block: "",
  blockLr: "5",
  blockChartOff: {},
  slice: "flows",
  report: null,
  reportKey: "",
  reportDetail: "",
  employees: null,
  employeesKey: "",
  employeesLoading: false,
  employeesError: "",
  badgeDraft: "",
  badgeQuery: "",
  empLr: "5",
  empLrMotion: "",
  empRows: false,
  empSort: "qty",
  empSortOpen: false,
  empPage: 0,
  empOpen: "",
  empDetail: null,
  empDetailLimit: 100,
  empCache: {},
  blocks: null,
  rowsKey: "",
  rowsLoading: false,
  rowsError: "",
  rowsReady: true,
  rowBlock: "",
  rowFloor: "",
  rowOpen: "",
  rowPage: 0,
  user: null,
};

const MODES = ["День", "Неделя", "Месяц", "Свободный"];
const DEFAULT_YEAR = String(new Date().getFullYear());
const VIEWS = ["dashboard", "flow", "group", "block_detail", "employees", "block_loss"];

const VIEW_DEPTH = { dashboard: 0, employees: 0, flow: 1, group: 2, block_detail: 3, block_loss: 1 };
let screenMotion = "";
let animating = 0;
let forkOpen = null;
let forkMotion = 0;

const ANCHOR_LABELS = {
  "День": "Выберите день",
  "Неделя": "Выберите неделю",
  "Месяц": "Выберите месяц",
};
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
let openPickerKind = "";

const NS = "http://www.w3.org/2000/svg";

