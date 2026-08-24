import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ModelSelector } from '../ModelSelector';
import type { ProviderModelsData } from '../types';

const sampleModels: Record<string, ProviderModelsData> = {
  openai: {
    display_name: 'OpenAI',
    models: ['gpt-4o', 'gpt-4o-mini'],
  },
  anthropic: {
    display_name: 'Anthropic',
    models: ['claude-sonnet-4-20250514'],
  },
};

describe('ModelSelector', () => {
  const defaultProps = {
    label: 'Default Model',
    description: 'The primary model for analysis',
    value: '',
    onChange: vi.fn(),
    models: sampleModels,
  };

  it('renders options grouped by provider using optgroup', () => {
    render(<ModelSelector {...defaultProps} />);

    // Label and description are rendered
    expect(screen.getByText('Default Model')).toBeInTheDocument();
    expect(screen.getByText('The primary model for analysis')).toBeInTheDocument();

    // optgroups present
    const optgroups = document.querySelectorAll('optgroup');
    expect(optgroups).toHaveLength(2);
    expect(optgroups[0]).toHaveAttribute('label', 'OpenAI');
    expect(optgroups[1]).toHaveAttribute('label', 'Anthropic');

    // Model options present within groups
    expect(screen.getByRole('option', { name: 'gpt-4o' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'gpt-4o-mini' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'claude-sonnet-4-20250514' })).toBeInTheDocument();
  });

  it('renders authored display_name instead of the catalog slug', () => {
    render(
      <ModelSelector
        {...defaultProps}
        models={{
          'minimax-cn': { display_name: 'MiniMax (CN)', models: ['minimax-cn-m3'] },
        }}
        metadata={{
          'minimax-cn-m3': { display_name: 'MiniMax-M3', model_id: 'MiniMax-M3' },
        }}
      />,
    );
    expect(screen.getByRole('option', { name: 'MiniMax-M3' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'minimax-cn-m3' })).toBeNull();
  });

  it('shows label and description', () => {
    render(<ModelSelector {...defaultProps} />);

    expect(screen.getByText('Default Model')).toBeInTheDocument();
    expect(screen.getByText('The primary model for analysis')).toBeInTheDocument();
  });

  it('fires onChange with selected model', () => {
    const onChange = vi.fn();
    render(<ModelSelector {...defaultProps} onChange={onChange} />);

    const select = document.querySelector('select')!;
    fireEvent.change(select, { target: { value: 'gpt-4o-mini' } });

    expect(onChange).toHaveBeenCalledWith('gpt-4o-mini');
  });

  it('shows "No models available" when no models', () => {
    render(
      <ModelSelector
        {...defaultProps}
        models={{}}
      />,
    );

    expect(screen.getByText('No models available')).toBeInTheDocument();
    expect(document.querySelector('select')).toBeNull();
  });
});
