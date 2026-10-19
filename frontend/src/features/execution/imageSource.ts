export const imageSource = (value: string): string => {
  if (!value) return '';
  if (/^data:image\/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$/.test(value)) return value;
  if (/^[A-Za-z0-9+/=]+$/.test(value)) return `data:image/png;base64,${value}`;
  return '';
};

export const imageFilename = (value: string): string => {
  if (value.startsWith('data:image/svg+xml;')) return 'plot.svg';
  if (value.startsWith('data:image/jpeg;')) return 'plot.jpg';
  if (value.startsWith('data:image/webp;')) return 'plot.webp';
  return 'plot.png';
};
