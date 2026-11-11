import { cn } from "cn"
import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { useForm } from "@tanstack/react-form"
import { useLocation, useNavigate } from "react-router"
import { RiClipboardLine, RiEyeLine, RiEyeOffLine, RiLoader4Line } from "@remixicon/react"

import { AuthInfoPanel } from "./auth-info-panel"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Field,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"

import { useAccountActions } from "@/hooks/use-auth"
import { getAuthInfo } from "@/lib/auth"

export function LoginForm({
  className,
  ...props
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { login: loginAccount } = useAccountActions();
  const [pasteError, setPasteError] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);

  const login = useMutation({
    mutationFn: loginAccount,
    onSuccess: () => {
      navigate(location.state?.from || '/', { replace: true });
    },
  });

  const view = useMutation({ mutationFn: (apiKey) => getAuthInfo(apiKey) });
  const isLoading = login.isPending || view.isPending;
  const requestError = view.error || login.error;
  const error = pasteError || ((requestError || location.state?.error) ? "User not found" : "");

  const form = useForm({
    onSubmit: ({ value }) => login.mutate(value.apiKey.trim()),
  });

  function handleSubmit(event) {
    event.preventDefault();
    view.reset();
    form.handleSubmit();
  }

  function handleView() {
    login.reset();
    view.mutate((form.getFieldValue('apiKey') || '').trim());
  }

  async function handlePaste() {
    try {
      const apiKey = await navigator.clipboard.readText();
      form.setFieldValue('apiKey', apiKey.trim());
      view.reset();
      login.reset();
      setPasteError("");
    } catch {
      setPasteError("Clipboard access is unavailable, paste directly into the field instead");
    }
  }

  return (
    <Card className={cn("p-0", className)} {...props}>
      <CardContent className="grid p-0 md:grid-cols-2">
        <form className="p-6 md:p-8" onSubmit={handleSubmit}>
          <FieldGroup>
            <div className="flex flex-col items-center gap-2 text-center">
              <h1 className="text-2xl font-bold">Welcome back</h1>
              <p className="text-balance text-muted-foreground">
                Login to your Pulse Premises account
              </p>
            </div>
            <form.Field name="apiKey">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor="api-key" required>API Key</FieldLabel>
                  <div className="relative">
                    <Input
                      id="api-key"
                      className="pr-16"
                      name={field.name}
                      value={field.state.value ?? ""}
                      onChange={(event) => {
                        field.handleChange(event.target.value);
                        view.reset();
                        login.reset();
                        setPasteError("");
                      }}
                      onBlur={field.handleBlur}
                      type={showApiKey ? "text" : "password"}
                      autoComplete="off"
                      autoCapitalize="none"
                      spellCheck={false}
                      disabled={isLoading}
                      aria-invalid={Boolean(error)}
                      aria-describedby={error ? "login-error" : undefined}
                      required
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="absolute top-0.5 right-8"
                      aria-label="Paste API key"
                      title="Paste API key"
                      onClick={handlePaste}
                      disabled={isLoading}
                    >
                      <RiClipboardLine aria-hidden="true" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="absolute top-0.5 right-0.5"
                      aria-label={showApiKey ? "Hide API key" : "Show API key"}
                      title={showApiKey ? "Hide API key" : "Show API key"}
                      aria-pressed={showApiKey}
                      onClick={() => setShowApiKey(!showApiKey)}
                      disabled={isLoading}
                    >
                      {showApiKey ? <RiEyeOffLine aria-hidden="true" /> : <RiEyeLine aria-hidden="true" />}
                    </Button>
                  </div>
                  <div className="h-[2lh] overflow-y-auto text-sm">
                    {error && <FieldError id="login-error">{error}</FieldError>}
                  </div>
                </Field>
              )}
            </form.Field>
            <Field className="grid grid-cols-[2fr_1fr] gap-3">
              <Button type="submit" disabled={isLoading} aria-busy={login.isPending}>
                {login.isPending && <RiLoader4Line className="animate-spin" aria-hidden="true" />}
                Login
              </Button>
              <Button variant="outline" type="button" onClick={handleView} disabled={isLoading} aria-busy={view.isPending}>
                View
              </Button>
            </Field>
          </FieldGroup>
        </form>
        <AuthInfoPanel data={view.data} isPending={view.isPending} />
      </CardContent>
    </Card>
  );
}
